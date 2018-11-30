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
    ).json()

    expires_in = r['expires_in']
    expired_at = from_now(expires_in)
    token = authtoken(r['access_token'], expired_at)

    return token


class Conversation:
    """ Wrapper on Activity object.

    See more:
        https://docs.microsoft.com/en-us/bot-framework/rest-api/bot-framework-rest-connector-api-reference#activity-object
    """

    def __init__(self, conversation):
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
    def reply_url(self):
        url = '/v3/conversations/{}/activities/{}'.format(
            self.conversation_id,
            self.activity_id
        )

        return urljoin(self.service_url, url)


class Identifier(Person):
    def __init__(self, obj_or_json):
        if isinstance(obj_or_json, str):
            subject = json.loads(obj_or_json)
        else:
            subject = obj_or_json

        self._subject = subject
        self._id = subject.get('id', '<not found>')
        self._name = subject.get('name', '<not found>')

    def __str__(self):
        return json.dumps({
            'id': self._id,
            'name': self._name
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


class BotFramework(ErrBot):
    """Errbot Backend for Bot Framework"""

    def __init__(self, config):
        super(BotFramework, self).__init__(config)

        identity = config.BOT_IDENTITY
        self._appId = identity.get('appId', None)
        self._appPassword = identity.get('appPassword', None)
        self._token = None
        self._emulator_mode = self._appId is None or self._appPassword is None

        self.bot_identifier = None
        self.default_conversation = None

    def _set_bot_identifier(self, identifier):
        self.bot_identifier = identifier

    def _ensure_token(self):
        """Keep OAuth token valid"""
        now = datetime.datetime.now()
        if not self._token or self._token.expired_at <= now:
            self._token = auth(self._appId, self._appPassword)
        return self._token.access_token

    def _build_reply(self, msg):
        log.debug("Debug reply message")
        log.debug(msg)
        log.debug(msg.extras)
        if 'conversation' in msg.extras:
            conversation = msg.extras['conversation']
        else:
            conversation = self.default_conversation
        payload = {
            'type': 'message',
            'conversation': conversation.conversation,
            'from': msg.to.subject,
            'recipient': msg.frm.subject,
            'replyToId': conversation.conversation_id,
            'text': msg.body
        }
        log.debug(payload)
        return activity(conversation.reply_url, payload)

    def _build_feedback(self, msg):
        conversation = msg.extras['conversation']
        payload = {
            'type': 'typing',
            'conversation': conversation.conversation,
            'from': msg.to.subject,
            'replyToId': conversation.conversation_id,
        }
        return activity(conversation.reply_url, payload)

    def _send_reply(self, response):
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

        r.raise_for_status()

    def serve_forever(self):
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
        response = self._build_reply(msg)
        self._send_reply(response)
        super(BotFramework, self).send_message(msg)

    def build_identifier(self, user):
        return Identifier(user)

    def build_reply(self, msg, text=None, private=False, threaded=False):
        return Message(
            body=text,
            parent=msg,
            frm=msg.frm,
            to=msg.to,
            extras=msg.extras,
        )

    def send_feedback(self, msg):
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
            log.debug(req)
            if req['type'] == 'message':
                msg = Message(req['text'])
                msg.frm = errbot.build_identifier(req['from'])
                msg.to = errbot.build_identifier(req['recipient'])
                msg.extras['conversation'] = errbot.build_conversation(req)
                errbot._set_bot_identifier(msg.to)
                self._set_bot_identifier(msg.to)
                self.default_conversation = errbot.build_conversation(req)
                errbot.send_feedback(msg)
                errbot.callback_message(msg)

            elif req['type'] == 'conversationUpdate' or req['type'] == 'contactRelationUpdate':
                bot_identifier = errbot.build_identifier(req['recipient'])
                errbot._set_bot_identifier(bot_identifier)
                self._set_bot_identifier(bot_identifier)
                conversation = errbot.build_conversation(req)
                errbot.default_conversation = conversation
                self.default_conversation = conversation
                # log.debug("Bot identifier:", errbot.bot_identifier.userid)
