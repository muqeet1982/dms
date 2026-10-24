"""
FBR Digital Invoicing (per FBR_INTEGRATION_GUIDE.md, verified against the live sandbox 24 Oct 2026).

Filing is OPT-IN per invoice (owner's rule): some customers get a tax invoice filed to FBR, most do not.
Nothing here runs unless somebody presses Validate/Submit on one invoice.
"""
from django.db import models

from apps.core.models import TenantModel


class FBRSettings(TenantModel):
    """One per tenant: identity, the two tokens, and the sandbox switch."""

    ntn = models.CharField(max_length=20, blank=True)                 # "1234567" or "A014841-0"
    cnic = models.CharField(max_length=20, blank=True)                # "42101-1234567-1"
    ntn_verified = models.BooleanField(default=False)
    ntn_reg_status = models.CharField(max_length=50, blank=True)      # from Get_Reg_Type
    pos_id = models.CharField(max_length=50, blank=True)
    business_name = models.CharField(max_length=255, blank=True, help_text="As registered with FBR, not a trading name")
    address = models.CharField(max_length=500, blank=True)
    province = models.CharField(max_length=40, blank=True, default="Punjab")

    # two tokens, one switch — never overwrite one with the other, never blank on an empty form field
    sandbox_token = models.CharField(max_length=1000, blank=True)
    production_token = models.CharField(max_length=1000, blank=True)
    use_sandbox = models.BooleanField(default=True)

    class Meta:
        verbose_name = "FBR settings"

    def token(self):
        """(token or None, use_sandbox)."""
        if self.use_sandbox:
            return (self.sandbox_token or None), True
        return (self.production_token or None), False


class FBRLookup(TenantModel):
    """
    The nine reference lists, cached (§4: fetch on demand from a sync action, never per request).
    `kind` is the endpoint; parametric results (rates per transaction type, schedules per rate,
    uoms per HS code) carry their parameter in `parent_key`.
    """

    KINDS = [("province", "Province"), ("doctype", "Document type"), ("hscode", "HS code"),
             ("sroitem", "SRO item"), ("transtype", "Transaction type"), ("uom", "Unit of measure"),
             ("sroschedule", "SRO schedule"), ("rate", "Rate"), ("hs_uom", "UoM for HS code")]
    kind = models.CharField(max_length=12, choices=KINDS, db_index=True)
    key = models.CharField(max_length=60)            # FBR's id for the row (hS_CODE, ratE_ID, …)
    description = models.CharField(max_length=500)
    parent_key = models.CharField(max_length=60, blank=True, default="", db_index=True)
    meta = models.JSONField(default=dict, blank=True)  # e.g. {"rate_value": 18}

    class Meta:
        unique_together = [("tenant", "kind", "key", "parent_key")]
        indexes = [models.Index(fields=["tenant", "kind", "parent_key"])]

    def __str__(self):
        return f"{self.kind}:{self.key} {self.description[:40]}"


class FBRFiling(TenantModel):
    """
    One invoice's journey to FBR — attached to the DMS invoice rather than bloating it. Holds the
    §11.6 snapshot, the verbatim §11.7 payload/response pairs, and the 28-character number.
    """

    STATUS = [("none", "Not filed"), ("validated", "Validated"), ("submitted", "Submitted"), ("failed", "Failed")]
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.CASCADE, related_name="fbr_filing")
    status = models.CharField(max_length=10, choices=STATUS, default="none", db_index=True)
    sandbox = models.BooleanField(default=True)       # which gateway this filing talked to

    # seller/buyer snapshot — the filed invoice keeps saying what it said when it was filed
    seller_ntn_cnic = models.CharField(max_length=20, blank=True)
    seller_business_name = models.CharField(max_length=255, blank=True)
    seller_province = models.CharField(max_length=40, blank=True)
    seller_address = models.CharField(max_length=500, blank=True)
    buyer_ntn_cnic = models.CharField(max_length=20, blank=True)
    buyer_business_name = models.CharField(max_length=255, blank=True)
    buyer_province = models.CharField(max_length=40, blank=True)
    buyer_address = models.CharField(max_length=500, blank=True)
    buyer_registration_type = models.CharField(max_length=30, blank=True)

    # transmission record — verbatim, success and failure alike
    last_payload = models.TextField(blank=True)
    validate_response = models.TextField(blank=True)
    validate_code = models.CharField(max_length=10, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    response = models.TextField(blank=True)
    response_code = models.CharField(max_length=10, blank=True)
    error_text = models.TextField(blank=True)
    fbr_invoice_number = models.CharField(max_length=100, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.invoice_id} → {self.status}"


class FBRScenarioTemplate(TenantModel):
    """FBR's sample payloads, stored verbatim (§10) — the runner replaces seller fields and date ONLY."""

    scenario_id = models.CharField(max_length=10)
    description = models.CharField(max_length=200, blank=True)
    payload = models.JSONField(default=dict)

    class Meta:
        unique_together = [("tenant", "scenario_id")]


class FBRScenarioRun(TenantModel):
    """Evidence: each run of a scenario, with what was sent and what came back."""

    scenario_id = models.CharField(max_length=10, db_index=True)
    passed = models.BooleanField(default=False)
    payload = models.TextField(blank=True)
    response = models.TextField(blank=True)
    status_code = models.CharField(max_length=10, blank=True)
    error_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
