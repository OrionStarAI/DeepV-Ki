"""
GitLab OAuth Client

This module handles the OAuth2 authentication with GitLab.
It uses Authlib to manage the OAuth2 flow.
"""

import logging
from authlib.integrations.starlette_client import OAuth
from api.config import GITLAB_CLIENT_ID, GITLAB_CLIENT_SECRET, GITLAB_URL

logger = logging.getLogger(__name__)

# Initialize OAuth registry
# We use a separate OAuth instance for GitLab to avoid conflicts with SSO if both are used (though unlikely in same app)
# But to keep it simple and consistent, we can reuse the same pattern.
oauth = OAuth()

def init_gitlab_oauth(app):
    """
    Initialize GitLab OAuth client with FastAPI app.
    This should be called during application startup.
    """
    if not GITLAB_CLIENT_ID or not GITLAB_CLIENT_SECRET:
        logger.warning("GITLAB_CLIENT_ID or GITLAB_CLIENT_SECRET not set. GitLab OAuth will not work.")
        return

    logger.info(f"Initializing GitLab OAuth with URL: {GITLAB_URL}")

    # Ensure GITLAB_URL doesn't end with slash for cleaner URL construction
    base_url = GITLAB_URL.rstrip('/')

    oauth.register(
        name='gitlab',
        client_id=GITLAB_CLIENT_ID,
        client_secret=GITLAB_CLIENT_SECRET,
        api_base_url=f'{base_url}/api/v4/',
        access_token_url=f'{base_url}/oauth/token',
        authorize_url=f'{base_url}/oauth/authorize',
        client_kwargs={
            'scope': 'api read_user openid profile email',
        }
    )
