from django.utils.cache import add_never_cache_headers
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed

class PreventSharedBrowserJWTMiddleware:
    """
    Middleware for DRF JWT architecture to prevent multi-user sharing 
    on the same computer/browser and disable API caching for back-arrow safety.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Try to authenticate the user via JWT for this incoming request
        request.user = None
        try:
            header = JWTAuthentication().get_header(request)
            if header:
                raw_token = JWTAuthentication().get_raw_token(header)
                validated_token = JWTAuthentication().get_validated_token(raw_token)
                request.user = JWTAuthentication().get_user(validated_token)
        except Exception:
            # If token validation fails, let DRF handle it normally in the views
            pass

        # 2. If authenticated, enforce the unique browser/tab restriction
        if request.user and request.user.is_authenticated:
            browser_tab_id = request.headers.get('X-Browser-Tab-ID') or request.COOKIES.get('browser_tab_id')
            
            # Extract the unique session/jti tracking identifier embedded in the JWT 
            # or from a secure cookie if your frontend sets one.
            # To strictly enforce one user per machine/browser profile:
            active_machine_user = request.COOKIES.get('active_platform_user')
            if active_machine_user and active_machine_user != str(request.user.id):
                # Another account is already active on this computer/browser profile
                raise AuthenticationFailed("Another user profile is currently active on this device. Please log out first.")

        # Process the request to get the API response
        response = self.get_response(request)

        # 3. Prevent Back-Arrow Caching on all API endpoints for logged-in users
        if request.user and request.user.is_authenticated:
            add_never_cache_headers(response)
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

        return response
