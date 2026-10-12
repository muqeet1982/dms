"""Mobile app distribution: platform admins upload APKs; anyone can fetch the current release, its QR code and the file."""
import hashlib
import io
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete
from django.http import FileResponse, Http404, HttpResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import IsPlatformAdmin

from .models import MobileAppRelease

MAX_APK_MB = 300


def public_download_url(platform="android"):
    return f"{settings.PUBLIC_BASE_URL}/get-app" if platform == "android" else f"{settings.PUBLIC_BASE_URL}/get-app?platform={platform}"


def api_download_url(platform="android"):
    return f"{settings.PUBLIC_BASE_URL}/api/v1/app/{platform}/download"


class MobileAppReleaseSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True, default="")
    download_url = serializers.SerializerMethodField()
    qr_url = serializers.SerializerMethodField()
    size_mb = serializers.SerializerMethodField()

    class Meta:
        model = MobileAppRelease
        fields = ["id", "platform", "version", "build_number", "file", "file_name", "size", "size_mb", "sha256", "notes", "min_supported_version", "is_current", "downloads", "uploaded_by_name", "created_at", "download_url", "qr_url"]
        read_only_fields = ["id", "file_name", "size", "sha256", "downloads", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def get_download_url(self, obj):
        return api_download_url(obj.platform)

    def get_qr_url(self, obj):
        return f"{settings.PUBLIC_BASE_URL}/api/v1/app/{obj.platform}/qr.png"

    def get_size_mb(self, obj):
        return round(obj.size / 1024 / 1024, 1)


class MobileAppReleaseViewSet(viewsets.ModelViewSet):
    """Platform admin: upload / list / set current / delete APK builds."""

    permission_classes = [IsPlatformAdmin]
    serializer_class = MobileAppReleaseSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = MobileAppRelease.objects.select_related("uploaded_by")
    filterset_fields = ["platform", "is_current"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_create(self, serializer):
        f = self.request.FILES.get("file")
        if f is None:
            raise DomainError("file is required (the .apk)", status_code=400)
        name = f.name.lower()
        platform = serializer.validated_data.get("platform", "android")
        if platform == "android" and not (name.endswith(".apk") or name.endswith(".aab")):
            raise DomainError("Android builds must be .apk (or .aab) files.", status_code=400)
        if f.size > MAX_APK_MB * 1024 * 1024:
            raise DomainError(f"File larger than {MAX_APK_MB} MB.", status_code=400)
        h = hashlib.sha256()
        for chunk in f.chunks():
            h.update(chunk)
        f.seek(0)
        raw = self.request.data.get("is_current")
        make_current = True if raw in (None, "") else str(raw).lower() in ("1", "true", "yes", "on")
        with transaction.atomic():
            if make_current:
                MobileAppRelease.objects.filter(platform=platform, is_current=True).update(is_current=False)
            rel = serializer.save(uploaded_by=self.request.user, file_name=f.name, size=f.size, sha256=h.hexdigest(), is_current=make_current)
        AuditService.log("platform.app_release.uploaded", rel, after={"platform": platform, "version": rel.version, "size": rel.size}, actor=self.request.user)

    def perform_update(self, serializer):
        rel = serializer.save()
        if rel.is_current:
            MobileAppRelease.objects.filter(platform=rel.platform, is_current=True).exclude(pk=rel.pk).update(is_current=False)

    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        rel = self.get_object()
        MobileAppRelease.objects.filter(platform=rel.platform, is_current=True).update(is_current=False)
        rel.is_current = True
        rel.save(update_fields=["is_current"])
        AuditService.log("platform.app_release.current", rel, after={"version": rel.version}, actor=request.user)
        return Response(MobileAppReleaseSerializer(rel).data)

    def perform_destroy(self, instance):
        AuditService.log("platform.app_release.deleted", instance, before={"version": instance.version, "file": instance.file.name}, actor=self.request.user)
        instance.delete()  # the APK itself is removed by `_delete_release_file` (post_delete signal)

    @action(detail=False, methods=["get", "patch"])
    def config(self, request):
        """App identity: Android package name, URL scheme, signing SHA-256 fingerprints (for App Links)."""
        from .models import PlatformSetting

        if request.method == "PATCH":
            cur = mobile_app_config()
            for k in MOBILE_APP_DEFAULTS:
                if k in request.data:
                    cur[k] = request.data[k]
            if isinstance(cur.get("sha256_fingerprints"), str):
                cur["sha256_fingerprints"] = [x.strip() for x in cur["sha256_fingerprints"].replace(",", "\n").splitlines() if x.strip()]
            PlatformSetting.set("mobile_app", cur)
            AuditService.log("platform.app_config.updated", model="PlatformSetting", after=cur, actor=request.user)
        cfg = mobile_app_config()
        return Response({**cfg, "assetlinks_url": f"{settings.PUBLIC_BASE_URL}/.well-known/assetlinks.json", "assetlinks_ready": bool(cfg["android_package"] and cfg["sha256_fingerprints"]), "app_login_url": f"{settings.PUBLIC_BASE_URL}/app-login"})

    @action(detail=False, methods=["get"])
    def storage(self, request):
        """What the APK folder holds on the server: files referenced by builds + orphans left behind by anything else."""
        return Response(_storage_report())

    @action(detail=False, methods=["post"], url_path="purge-old")
    def purge_old(self, request):
        """Delete every build that is not the current one (per platform) and sweep orphaned APK files."""
        platform = request.data.get("platform") or request.query_params.get("platform") or "android"
        old = list(MobileAppRelease.objects.filter(platform=platform, is_current=False))
        freed = 0
        for rel in old:
            freed += rel.size or 0
            AuditService.log("platform.app_release.deleted", rel, before={"version": rel.version, "file": rel.file.name}, actor=request.user)
            rel.delete()
        report = _storage_report(sweep=True)
        return Response({"deleted": len(old), "freed_mb": round(freed / 1048576, 1), "orphans_removed": report["orphans_removed"], **report})


MOBILE_APP_DEFAULTS = {"android_package": "", "scheme": "dms", "sha256_fingerprints": [], "ios_bundle_id": ""}


def mobile_app_config():
    """App identity used for deep links (dms://…), Android App Links (/.well-known/assetlinks.json) and intent URLs."""
    from .models import PlatformSetting

    cfg = {**MOBILE_APP_DEFAULTS, **(PlatformSetting.get("mobile_app", {}) or {})}
    cfg["scheme"] = (cfg.get("scheme") or "dms").strip().lower()
    cfg["sha256_fingerprints"] = [f.strip().upper() for f in (cfg.get("sha256_fingerprints") or []) if f and f.strip()]
    return cfg


class AssetLinksView(APIView):
    """GET /.well-known/assetlinks.json — lets Android open https://<host>/app-login?t=… directly in the app (App Links)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        cfg = mobile_app_config()
        if not cfg["android_package"] or not cfg["sha256_fingerprints"]:
            return HttpResponse("[]", content_type="application/json")
        import json

        body = [{"relation": ["delegate_permission/common.handle_all_urls"], "target": {"namespace": "android_app", "package_name": cfg["android_package"], "sha256_cert_fingerprints": cfg["sha256_fingerprints"]}}]
        return HttpResponse(json.dumps(body), content_type="application/json")


def _delete_release_file(sender, instance, **kwargs):
    """post_delete: remove the APK from disk whenever a release row goes away (API delete, purge, shell)."""
    if instance.file:
        try:
            instance.file.delete(save=False)
        except Exception:  # pragma: no cover - storage hiccup; the orphan sweep picks it up later
            logging.getLogger(__name__).warning("Could not delete APK file %s", instance.file.name, exc_info=True)


post_delete.connect(_delete_release_file, sender=MobileAppRelease, dispatch_uid="platform.app_release.delete_file")


def _storage_report(sweep=False):
    """List files under MEDIA_ROOT/apps and compare with the releases table; `sweep=True` deletes unreferenced files."""
    storage = MobileAppRelease._meta.get_field("file").storage
    referenced = {r.file.name for r in MobileAppRelease.objects.all() if r.file}
    try:
        _dirs, names = storage.listdir("apps")
    except (FileNotFoundError, NotADirectoryError):
        names = []
    files, orphans, total = [], [], 0
    for n in sorted(names):
        path = f"apps/{n}"
        size = storage.size(path)
        total += size
        entry = {"name": n, "size_mb": round(size / 1048576, 1), "referenced": path in referenced}
        files.append(entry)
        if path not in referenced:
            orphans.append(path)
    removed = 0
    if sweep:
        for path in orphans:
            storage.delete(path)
            removed += 1
        files = [f for f in files if f["referenced"]]
        total = sum(storage.size(f"apps/{f['name']}") for f in files)
    return {"files": files, "count": len(files), "total_mb": round(total / 1048576, 1), "orphans": 0 if sweep else len(orphans), "orphans_removed": removed}


def _current(platform):
    rel = MobileAppRelease.objects.filter(platform=platform, is_current=True).first() or MobileAppRelease.objects.filter(platform=platform).first()
    if rel is None:
        raise Http404("No app build uploaded yet.")
    return rel


class PublicAppInfoView(APIView):
    """Public: current build info for the QR modal / get-app page."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, platform="android"):
        rel = MobileAppRelease.objects.filter(platform=platform, is_current=True).first() or MobileAppRelease.objects.filter(platform=platform).first()
        cfg = mobile_app_config()
        identity = {"package_name": cfg["android_package"], "scheme": cfg["scheme"], "app_login_url": f"{settings.PUBLIC_BASE_URL}/app-login", "app_links_ready": bool(cfg["android_package"] and cfg["sha256_fingerprints"])}
        if rel is None:
            return Response({"available": False, "platform": platform, "page_url": public_download_url(platform), **identity})
        return Response({
            "available": True, "platform": platform, "version": rel.version, "build_number": rel.build_number, "size": rel.size, "size_mb": round(rel.size / 1024 / 1024, 1), "sha256": rel.sha256,
            "notes": rel.notes, "min_supported_version": rel.min_supported_version, "released_at": rel.created_at, "file_name": rel.file_name,
            "download_url": api_download_url(platform), "page_url": public_download_url(platform), "qr_url": f"{settings.PUBLIC_BASE_URL}/api/v1/app/{platform}/qr.png", **identity,
        })


class PublicAppDownloadView(APIView):
    """Public: streams the current APK with the right content type so phones install it directly."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, platform="android"):
        rel = _current(platform)
        MobileAppRelease.objects.filter(pk=rel.pk).update(downloads=rel.downloads + 1)
        ext = (rel.file_name or "app.apk").rsplit(".", 1)[-1].lower()
        filename = f"dms-{rel.version}.{ext}".replace(" ", "-")
        content_type = "application/vnd.android.package-archive" if ext == "apk" else "application/octet-stream"
        resp = FileResponse(rel.file.open("rb"), content_type=content_type, as_attachment=True, filename=filename)
        resp["Content-Length"] = rel.size or rel.file.size
        resp["X-App-Version"] = rel.version
        return resp


class PublicAppQrView(APIView):
    """Public: PNG QR code pointing at the get-app page (scan with the phone camera)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, platform="android"):
        import segno

        target = request.query_params.get("target") or public_download_url(platform)
        if not target.startswith(settings.PUBLIC_BASE_URL):
            target = public_download_url(platform)
        buf = io.BytesIO()
        segno.make(target, error="m").save(buf, kind="png", scale=int(request.query_params.get("scale", 8)), border=2, dark="#202C4B")
        resp = HttpResponse(buf.getvalue(), content_type="image/png")
        resp["Cache-Control"] = "public, max-age=3600"
        return resp
