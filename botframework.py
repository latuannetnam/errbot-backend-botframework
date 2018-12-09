import json
import logging
import datetime
import requests

from time import sleep
from urllib.parse import urljoin
from collections import namedtuple

from bottle import request
from errbot.core import ErrBot
from errbot.core_plugins.wsview import bottle_app
from errbot.backends.base import Message, Person

log = logging.getLogger('errbot.backends.botframework')

authtoken = namedtuple('AuthToken', 'access_token, expired_at')
activity = namedtuple('Activity', 'post_url, payload')
CHANNEL_LIST = 'CHANNEL_LIST'

# channel_list = {
#     "skype": {
#         "serviceUrl": "https://smba.trafficmanager.net/apis",
#         "bot_identifier": {
#             "id": "28:424ae5c1-d009-407a-b887-3c2e491c71b7",
#             "name": "netnammonbot"
#         },
#     },
#     "telegram": {
#         "serviceUrl": "https://telegram.botframework.com",
#         "bot_identifier": {
#             "id": "netnammonbot",
#             "name": "mybot_latuan"
#         },
#     },
# }


def from_now(seconds):
    now = datetime.datetime.now()
    return now + datetime.timedelta(seconds=seconds)


def auth(appId, appPasswd):
    form = {
        'grant_type': 'client_credentials',
        'scope': 'https://api.botframework.com/.default',
        'client_id': appId,
        'client_secret': appPasswd,
    }

    r = requests.post(
        'https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token',
        data=form
    )
    log.debug("Auth status code:{}". format(r.status_code))
    if r.status_code > 400:
        log.warn("Can not Authenticate. Error:%s. Message:%s",
                 r.status_code, r.text)
        return None
    auth_req = r.json()
    # log.debug("Authentication response:{}".format(auth_req))
    expires_in = auth_req['expires_in']
    expired_at = from_now(expires_in)
    token = authtoken(auth_req['access_token'], expired_at)

    return token


class Conversation:
    """ Wrapper on Activity object.

    See more:
        https://docs.microsoft.com/en-us/bot-framework/rest-api/bot-framework-rest-connector-api-reference#activity-object
    """

    def __init__(self, conversation=None):
        self._conversation = conversation

    @property
    def conversation(self):
        return self._conversation['conversation']

    @property
    def conversation_id(self):
        return self.conversation['id']

    @property
    def activity_id(self):
        return self._conversation['id']

    @property
    def service_url(self):
        return self._conversation['serviceUrl']

    @property
    def user(self):
        return Identifier(self._conversation['from'])

    @property
    def channel(self):
        return self._conversation['channelId']

    @property
    def reply_url(self):
        url = '/v3/conversations/{}/activities/{}'.format(
            self.conversation_id,
            self.activity_id
        )
        return urljoin(self.service_url, url)

    @property
    def send_url(self):
        url = '/v3/conversations/{}/activities'.format(
            self.conversation_id
        )

        return urljoin(self.service_url, url)


class Identifier(Person):
    def __init__(self, obj_or_json, channel=None):
        if isinstance(obj_or_json, str):
            subject = json.loads(obj_or_json)
        else:
            subject = obj_or_json

        self._subject = subject
        self._id = subject.get('id', '<not found>')
        self._name = subject.get('name', '<not found>')
        if channel is not None:
            self._channel = channel
        else:
            self._channel = None

    def __str__(self):
        return json.dumps({
            'id': self._id,
            'name': self._name,
            'channel': self._channel
        })

    def __eq__(self, other):
        return str(self) == str(other)

    @property
    def subject(self):
        return self._subject

    @property
    def userid(self):
        return self._id

    @property
    def aclattr(self):
        return self._id

    @property
    def person(self):
        return self._name

    @property
    def nick(self):
        return self._name

    @property
    def fullname(self):
        return self._name

    @property
    def client(self):
        return '<not set>'
    
    @property
    def channel(self):
        return self._channel


class Channel:
    def __init__(self, serviceUrl, bot_identifier):
        self.serviceUrl = serviceUrl
        self.bot_identifier = bot_identifier
        self.conversation_list = {}


class BotFramework(ErrBot):
    """Errbot Backend for Bot Framework"""

    def __init__(self, config):
        super(BotFramework, self).__init__(config)
        identity = config.BOT_IDENTITY
        if hasattr(config, "BOTFRAMEWORK"):
            self.botframework = config.BOTFRAMEWORK
            log.debug("botframework:%s", json.dumps(
                self.botframework, indent=4))
        else:
            self.botframework = None
        self._appId = identity.get('appId', None)
        self._appPassword = identity.get('appPassword', None)
        self._token = None
        self._emulator_mode = self._appId is None or self._appPassword is None
        self.bot_identifier = None
        log.debug("Done init backend")

    def _set_bot_identifier(self, identifier):
        self.bot_identifier = identifier

    def get_bot_identifier(self, channel_id):
        if channel_id not in self.channel_list:
            return None
        return self.channel_list[channel_id].bot_identifier

    def _ensure_token(self):
        """Keep OAuth token valid"""
        now = datetime.datetime.now()
        if not self._token or self._token.expired_at <= now:
            self._token = auth(self._appId, self._appPassword)
        return self._token.access_token

    def _build_reply(self, msg):
        log.debug("calling self._build_reply:%s", msg)
        # log.debug("reply message:[%s]", msg)
        # log.debug("reply msg extra:[%s", msg.extras)
        payload = {}
        if 'conversation' in msg.extras:
            conversation = msg.extras['conversation']
            payload = {
                'type': 'message',
                'conversation': conversation.conversation,
                'from': msg.to.subject,
                'recipient': msg.frm.subject,
                'replyToId': conversation.conversation_id,
                'text': msg.body
            }
            log.debug("reply url:[%s]", conversation.reply_url)
            log.debug("payload:[%s]", payload)
            return activity(conversation.reply_url, payload)
        else:
            log.warn("Can not determine conversation")
            return None

    def _build_feedback(self, msg):
        log.debug("Calling self._build_feedback")
        conversation = msg.extras['conversation']
        payload = {
            'type': 'typing',
            'conversation': conversation.conversation,
            'from': msg.to.subject,
            'replyToId': conversation.conversation_id,
        }
        return activity(conversation.reply_url, payload)

    def _build_send(self, msg):
        log.debug("calling self._build_send:%s", msg)
        # log.debug("reply message:[%s]", msg)
        # log.debug("reply msg extra:[%s", msg.extras)
        payload = {}
        if 'conversation' in msg.extras:
            conversation = msg.extras['conversation']
            payload = {
                'type': 'message',
                'conversation': conversation.conversation,
                'from': msg.frm.subject,
                'recipient': msg.to.subject,
                'replyToId': conversation.conversation_id,
                'text': msg.body
            }
            log.debug("send url:[%s]", conversation.send_url)
            log.debug("payload:[%s]", payload)
            return activity(conversation.send_url, payload)
        else:
            log.warn("Can not determine conversation")
            return None

    def _send_reply(self, response):
        log.debug("Calling self._send_reply")
        """Post response to callback url

        Send a reply to URL indicated in serviceUrl from
        Bot Framework's request object.

        @param response: activity object
        """
        headers = {
            'Content-Type': 'application/json'
        }

        if not self._emulator_mode:
            access_token = self._ensure_token()
            headers['Authorization'] = 'Bearer ' + access_token

        r = requests.post(
            response.post_url,
            data=json.dumps(response.payload),
            headers=headers
        )

        if r.status_code >= 400:
            log.warn("Can not send message. Error:%s. Message:%s",
                     r.status_code, r.text)
        r.raise_for_status()

    def _create_conversation(self, channel_id, channel_userid):
        if channel_id not in self.channel_list:
            log.warn("%s not in channel_list", channel_id)
            return None
        headers = {
            'Content-Type': 'application/json'
        }

        if not self._emulator_mode:
            access_token = self._ensure_token()
            headers['Authorization'] = 'Bearer ' + access_token

        request_url = urljoin(
            self.channel_list[channel_id].serviceUrl, "/v3/conversations")
        bot_identifier = self.channel_list[channel_id].bot_identifier
        payload = {
            "bot": {
                "id": bot_identifier.userid,
                # "name": bot_identifier.person
            },
            # "isGroup": False,
            "members": [{
                "id": channel_userid,
                # "name": "User"
            }],
            # "topicName": "Proactive conversation",
        }

        log.debug("request url:%s", request_url)
        log.debug("playload:%s", payload)
        r = requests.post(
            request_url,
            data=json.dumps(payload),
            headers=headers
        )

        log.debug("result:[%s]", r)
        if 200 <= r.status_code <= 300:
            json_data = r.json()
            conversation_id = json_data["id"]
            req = {
                "serviceUrl": self.channel_list[channel_id].serviceUrl,
                "conversation": {
                    "id": conversation_id
                },
                "id": conversation_id,
                "from": {
                    "name": "User",
                    "id": channel_userid
                },
            }
            conversation = self.build_conversation(req)
            self.channel_list[channel_id].conversation_list[channel_userid] = conversation
            log.debug("Conversation:%s created for channel:%s and user:%s",
                      conversation_id, channel_id, channel_userid)
            return conversation
        else:
            log.warn("Can not create conversation. Error:%s. Message:%s",
                     r.status_code, r.text)

        r.raise_for_status()

    def get_conversations(self, channel_id):
        headers = {
            'Content-Type': 'application/json'
        }

        if not self._emulator_mode:
            access_token = self._ensure_token()
            headers['Authorization'] = 'Bearer ' + access_token
        get_url = urljoin(channel_list[channel_id]
                          ["serviceUrl"], "/v3/conversations")
        r = requests.get(
            get_url,
            headers=headers
        )

        if 200 <= r.status_code <= 300:
            # results = r.json()['results']
            log.debug("result:[%s]", r)
        else:
            log.warn("Warning:%s. Message:%s", r.status_code, r.text)
        r.raise_for_status()

    def _init_default(self):
        log.debug("Init default variables")
        if self.botframework is None:
            channel_list = {}
        else:
            channel_list = self.botframework.get("channel_list", {})
        log.debug("Init channel list")
        self.channel_list = {}
        for channel_name in channel_list:
            channel = channel_list[channel_name]
            self.channel_list[channel_name] = Channel(serviceUrl=channel["serviceUrl"],
                                                      bot_identifier=Identifier(
                {
                    "id": channel["bot_identifier"]["id"],
                    "name": channel["bot_identifier"]["name"],
                }
            )
            )

    def serve_forever(self):
        self._init_default()
        self._init_handler(self)
        self.connect_callback()

        try:
            while True:
                sleep(1)
        except KeyboardInterrupt:
            log.info("Interrupt received, shutting down")
        finally:
            self.disconnect_callback()
            self.shutdown()

    def send_message(self, msg):
        log.debug("Calling self.send_message:%s", msg)
        log.debug("message extras:%s", msg.extras)
        log.debug("to:%s", msg.to.userid)
        response = None
        if 'conversation' not in msg.extras:
            channel_id, channel_userid = msg.to.userid.split(".")
            log.debug("Build conversation for channel:%s and user:%s",
                      channel_id, channel_userid)
            if channel_id is not None and channel_userid is not None:
                if channel_id in self.channel_list and channel_userid in self.channel_list[channel_id].conversation_list:
                    conversation = self.channel_list[channel_id].conversation_list[channel_userid]
                else:
                    log.debug(
                        "Create converation for channel %s and user %s", channel_id, channel_userid)
                    conversation = self._create_conversation(
                        channel_id, channel_userid)
                if conversation is not None:
                    msg.extras['conversation'] = conversation
                    msg.to = self.build_identifier({"id": channel_userid})
                    msg.frm = self.channel_list[channel_id].bot_identifier
                    response = self._build_send(msg)
        else:
            response = self._build_reply(msg)

        if response is not None:
            self._send_reply(response)
        super(BotFramework, self).send_message(msg)

    def build_identifier(self, user, channel=None):
        if channel is not None:
            return Identifier(user, channel)
        else:
            return Identifier(user)

    def build_reply(self, msg, text=None, private=False, threaded=False):
        log.debug("Calling self.build_reply")
        return Message(
            body=text,
            parent=msg,
            frm=msg.frm,
            to=msg.to,
            extras=msg.extras,
        )

    def send_feedback(self, msg):
        log.debug("Calling self.send_feedback")
        feedback = self._build_feedback(msg)
        self._send_reply(feedback)

    def build_conversation(self, conv):
        return Conversation(conv)

    def change_presence(self, status, message):
        pass

    def query_room(self, room):
        return None

    def rooms(self):
        return []

    @property
    def mode(self):
        return 'BotFramework'

    def _init_handler(self, errbot):
        @bottle_app.route('/botframework', method=['GET', 'OPTIONS'])
        def get_botframework():
            pass

        @bottle_app.route('/botframework', method=['POST'])
        def post_botframework():
            req = request.json
            log.debug('received request: type=[%s] channel=[%s]',
                      req['type'], req['channelId'])
            log.debug(json.dumps(req, indent=4))
            if (req['type'] == 'message') and ('text' in req):
                msg = Message(req['text'])
            else:
                msg = Message()
            channel_id = None
            if "channelId" in req:
                channel_id = req["channelId"]
            msg.frm = self.build_identifier(req['from'], channel_id)
            msg.to = self.build_identifier(req['recipient'], channel_id)
            msg.extras['conversation'] = self.build_conversation(req)
            bot_identifier = msg.to
            self._set_bot_identifier(msg.to)
            isGroup = False
            if "isGroup" in req["conversation"]:
                isGroup = req["conversation"]["isGroup"]
            log.debug("isGroup:{}".format(isGroup))
            if channel_id is not None and isGroup is False:
                if channel_id not in self.channel_list:
                    log.debug("init channel:%s", channel_id)
                    log.debug("bot identifier:%s", msg.to)
                    channel = Channel(req["serviceUrl"], msg.to)
                else:
                    channel = self.channel_list[channel_id]
                    if channel.serviceUrl != req["serviceUrl"]:
                        channel.serviceUrl = req["serviceUrl"]
                        log.debug("update serviceUrl:%s for channel:%s",
                                  channel.serviceUrl, channel_id)
                channel.conversation_list[msg.frm.userid] = msg.extras['conversation']
                self.channel_list[channel_id] = channel

            self.send_feedback(msg)
            self.callback_message(msg)
