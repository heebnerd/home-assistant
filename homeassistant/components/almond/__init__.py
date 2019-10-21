"""
Support for Somfy hubs.

For more details about this component, please refer to the documentation at
https://home-assistant.io/integrations/somfy/
"""

from aiohttp import ClientResponse, ClientSession
from pyalmond import AbstractAlmondAuth, WebAlmondAPI
import voluptuous as vol


from homeassistant.const import CONF_TYPE, CONF_HOST
from homeassistant.helpers import (
    config_validation as cv,
    config_entry_oauth2_flow,
    intent,
    aiohttp_client,
)
from homeassistant import config_entries
from homeassistant.components import conversation

from . import config_flow
from .const import DOMAIN, TYPE_LOCAL, TYPE_OAUTH2

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

DEFAULT_OAUTH2_HOST = "https://almond.stanford.edu"
DEFAULT_LOCAL_HOST = "http://localhost:3000"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Any(
            vol.Schema(
                {
                    vol.Required(CONF_TYPE): TYPE_OAUTH2,
                    vol.Required(CONF_CLIENT_ID): cv.string,
                    vol.Required(CONF_CLIENT_SECRET): cv.string,
                    vol.Optional(CONF_HOST, default=DEFAULT_OAUTH2_HOST): cv.url,
                }
            ),
            vol.Schema(
                {vol.Required(CONF_TYPE): TYPE_LOCAL, vol.Required(CONF_HOST): cv.url}
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Set up the Somfy component."""
    hass.data[DOMAIN] = {}

    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]

    host = conf[CONF_HOST]

    if conf[CONF_TYPE] == TYPE_OAUTH2:
        config_flow.AlmondFlowHandler.async_register_implementation(
            hass,
            config_entry_oauth2_flow.LocalOAuth2Implementation(
                hass,
                DOMAIN,
                conf[CONF_CLIENT_ID],
                conf[CONF_CLIENT_SECRET],
                f"{host}/me/api/oauth2/authorize",
                f"{host}/me/api/oauth2/token",
            ),
        )
        return True

    if not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data={"type": TYPE_LOCAL, "host": conf[CONF_HOST]},
            )
        )
    return True


async def async_setup_entry(hass, entry):
    """Set up Almond config entry."""
    if entry.data["type"] == TYPE_LOCAL:
        auth = AlmondLocal(
            entry.data["host"], aiohttp_client.async_get_clientsession(hass)
        )

    else:
        # OAuth2
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
        oauth_session = config_entry_oauth2_flow.OAuth2Session(
            hass, entry, implementation
        )
        auth = AlmondOAuth(entry.data["host"], oauth_session)

    conversation.async_set_agent(hass, AlmondAgent(WebAlmondAPI(auth)))

    return True


async def async_unload_entry(hass, entry):
    """Unload Almond."""
    conversation.async_set_agent(hass, None)
    return True


class AlmondOAuth(AbstractAlmondAuth):
    """Almond Authentication using OAuth2."""

    def __init__(
        self, host: str, oauth_session: config_entry_oauth2_flow.OAuth2Session
    ):
        """Initialize Almond auth."""
        super().__init__(host)
        self._oauth_session = oauth_session

    async def post(self, url, **kwargs) -> ClientResponse:
        """Make a post request."""
        return await self._oauth_session.async_request(
            "post", f"{self.host}{url}", **kwargs
        )


class AlmondLocal(AbstractAlmondAuth):
    """Almond Authentication using a local unauthenticated connection."""

    def __init__(self, host: str, websession: ClientSession):
        """Initialize Almond auth."""
        super().__init__(host)
        self._websession = websession

    async def post(self, url, **kwargs) -> ClientResponse:
        """Make a post request."""
        return await self._websession.request(
            "post",
            f"{self.host}{url}",
            **kwargs,
            headers={
                **(kwargs.get("headers") or {}),
                "origin": "http://127.0.0.1:3000",
            },
        )


class AlmondAgent(conversation.AbstractConversationAgent):
    """Almond conversation agent."""

    def __init__(self, api: WebAlmondAPI):
        """Initialize the agent."""
        self.api = api

    async def async_process(self, text: str) -> intent.IntentResponse:
        """Process a sentence."""
        response = await self.api.async_converse_text(text)

        buffer = ""
        for message in response["messages"]:
            if message["type"] == "text":
                buffer += "\n" + message["text"]
            elif message["type"] == "picture":
                buffer += "\n Picture: " + message["url"]
            elif message["type"] == "rdl":
                buffer += (
                    "\n Link: "
                    + message["rdl"]["displayTitle"]
                    + " "
                    + message["rdl"]["webCallback"]
                )
            elif message["type"] == "choice":
                buffer += "\n Choice: " + message["title"]

        intent_result = intent.IntentResponse()
        intent_result.async_set_speech(buffer.strip())
        return intent_result
