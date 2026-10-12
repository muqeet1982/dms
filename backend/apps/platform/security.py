"""
Server security for platform admins: fail2ban bans (list, unban, block, trust) and the ufw firewall.

Nothing here touches the firewall directly — it shells out to one root helper installed by
`deploy/security/install-security.sh`:

    sudo /usr/local/bin/dms-secops <command> [args]

The helper validates every argument again and returns JSON. If it is not installed yet, the endpoints answer
with `installed: false` and the admin pages explain how to install it instead of failing.
"""

import ipaddress
import json
import shutil
import subprocess

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import IsPlatformAdmin

HELPER = "/usr/local/bin/dms-secops"
SUDO = shutil.which("sudo") or "/usr/bin/sudo"
INSTALL_HINT = "The security helper is not installed yet. Run `sudo bash deploy/security/install-security.sh` on the server (it installs the fail2ban jails, the ufw policy and the root helper this page uses)."


def helper_installed() -> bool:
    return shutil.which(HELPER) is not None or shutil.os.path.exists(HELPER)


def _argv(args):
    """
    Drop an optional flag whose value is empty — `("bans", "--jail", None)` must become `bans`, not a dangling
    `--jail` that argparse then rejects.
    """
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if isinstance(a, str) and a.startswith("--"):
            value = args[i + 1] if i + 1 < len(args) else None
            if value not in (None, ""):
                out += [a, str(value)]
            i += 2
            continue
        if a not in (None, ""):
            out.append(str(a))
        i += 1
    return out


def secops(*args, timeout=35):
    """Run one helper command and return its JSON. Raises DomainError with the helper's message on failure."""
    if not helper_installed():
        raise DomainError(INSTALL_HINT, code="helper_missing", status_code=503)
    # the service runs with PATH set to the virtualenv only, so sudo is called by its full path
    cmd = [SUDO, "-n", HELPER, *_argv(args)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise DomainError("The firewall command timed out.", code="timeout", status_code=504) from e
    out = (p.stdout or "").strip()
    try:
        data = json.loads(out)
    except ValueError as e:
        detail = (p.stderr or out or "no output").strip()[:300]
        if "password is required" in detail or "not allowed to execute" in detail:
            raise DomainError("The web app is not allowed to run the security helper — re-run deploy/security/install-security.sh to install the sudoers rule.", code="sudo", status_code=503) from e
        raise DomainError(f"Security helper failed: {detail}", code="helper_error", status_code=502) from e
    if not data.get("ok", True):
        raise DomainError(data.get("error", "Security helper refused the request."), code="helper_error", status_code=400)
    return data


def check_ip(value):
    try:
        return str(ipaddress.ip_network(value, strict=False)) if "/" in str(value) else str(ipaddress.ip_address(value))
    except ValueError as e:
        raise DomainError(f"'{value}' is not a valid IP address or network.", status_code=400) from e


def paginate(rows, request, default_size=25):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        size = min(200, max(5, int(request.query_params.get("page_size", default_size))))
    except (TypeError, ValueError):
        page, size = 1, default_size
    start = (page - 1) * size
    return {"count": len(rows), "page": page, "page_size": size, "pages": max(1, -(-len(rows) // size)), "results": rows[start : start + size]}


class SecurityStatusView(APIView):
    """GET /platform/security/status — firewall + fail2ban overview, listening ports and the caller's own IP."""

    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        base = {"installed": helper_installed(), "install_hint": INSTALL_HINT, "your_ip": getattr(request, "client_ip", None)}
        if not base["installed"]:
            return Response(base)
        return Response({**base, **secops("status")})


class BanListView(APIView):
    """GET (paginated, `?q=` `?jail=`) / POST { ip, action: unban|block|trust|untrust, note } — fail2ban bans."""

    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        if not helper_installed():
            return Response({"installed": False, "install_hint": INSTALL_HINT, "count": 0, "results": [], "your_ip": getattr(request, "client_ip", None)})
        data = secops("bans", "--jail", request.query_params.get("jail"), "--q", request.query_params.get("q"))
        trusted = secops("trusted")["results"]
        return Response({"installed": True, "your_ip": getattr(request, "client_ip", None), "jails": secops("jails")["results"], "trusted": trusted, **paginate(data["results"], request)})

    def post(self, request):
        action = request.data.get("action")
        ip = check_ip(request.data.get("ip"))
        note = request.data.get("note", "")
        if action == "unban":
            out = secops("unban", ip)
        elif action == "block":
            out = secops("ban", ip)
        elif action == "trust":
            out = secops("trust", ip, "--note", note, "--by", request.user.username)
        elif action == "untrust":
            out = secops("untrust", ip)
        else:
            raise DomainError("action must be unban, block, trust or untrust.", status_code=400)
        AuditService.log(f"security.{action}", model="Firewall", object_id=ip, after={"ip": ip, "note": note}, actor=request.user, request=request)
        return Response(out)


class FirewallView(APIView):
    """GET rules + port verdicts / POST { action: allow_ip|deny_ip|delete|allow_port|deny_port, … } — ufw."""

    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        if not helper_installed():
            return Response({"installed": False, "install_hint": INSTALL_HINT, "rules": [], "ports": []})
        return Response({"installed": True, "your_ip": getattr(request, "client_ip", None), "rules": secops("fw-rules")["results"], **{k: v for k, v in secops("ports").items() if k != "ok"}, "status": secops("status")["firewall"]})

    def post(self, request):
        action = request.data.get("action")
        note = request.data.get("note", "")
        if action == "allow_ip":
            out = secops("fw-allow", check_ip(request.data.get("ip")), "--port", request.data.get("port"), "--proto", request.data.get("proto"), "--note", note)
        elif action == "deny_ip":
            out = secops("fw-deny", check_ip(request.data.get("ip")))
        elif action == "delete":
            out = secops("fw-delete", request.data.get("num"))
        elif action == "allow_port":
            out = secops("fw-port-allow", request.data.get("port"), "--proto", request.data.get("proto"), "--note", note)
        elif action == "deny_port":
            out = secops("fw-port-deny", request.data.get("port"), "--proto", request.data.get("proto"))
        else:
            raise DomainError("action must be allow_ip, deny_ip, delete, allow_port or deny_port.", status_code=400)
        AuditService.log(f"firewall.{action}", model="Firewall", object_id=str(request.data.get("ip") or request.data.get("port") or request.data.get("num")), after=dict(request.data), actor=request.user, request=request)
        return Response(out)
