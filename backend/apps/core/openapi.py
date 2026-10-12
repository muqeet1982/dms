from drf_spectacular.extensions import OpenApiAuthenticationExtension


class TenantJWTScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.TenantJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT", "description": "Access token from /api/v1/auth/login. Send X-Tenant-ID as well when the user belongs to several companies."}


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "apps.integrations.authentication.ApiKeyAuthentication"
    name = "apiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-API-Key"}
