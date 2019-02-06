import io
import json
import logging
import uuid
import wave
import time
import threading
from typing import Dict, Any, Optional, List
from collections import defaultdict

import paho.mqtt.client as mqtt
from thespian.actors import ActorAddress

from .actor import RhasspyActor

# -----------------------------------------------------------------------------
# Events
# -----------------------------------------------------------------------------

class MqttPublish:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload

class MqttSubscribe:
    def __init__(self,
                 topic: str,
                 receiver:Optional[ActorAddress]=None) -> None:
        self.topic = topic
        self.receiver = receiver

class MqttConnected:
    pass

class MqttDisconnected:
    pass

class MqttMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload

# -----------------------------------------------------------------------------
# Interoperability with Snips.AI Hermes protocol
# https://docs.snips.ai/ressources/hermes-protocol
# -----------------------------------------------------------------------------

class HermesMqtt(RhasspyActor):
    def __init__(self) -> None:
        RhasspyActor.__init__(self)
        self.client = None
        self.connected = False
        self.subscriptions:Dict[str, List[ActorAddress]] = defaultdict(list)
        self.publications:Dict[str, List[bytes]] = defaultdict(list)

    # -------------------------------------------------------------------------

    def to_started(self, from_state:str) -> None:
        # Load settings
        self.site_id = self.profile.get('mqtt.site_id', 'default')
        self.host = self.profile.get('mqtt.host', 'localhost')
        self.port = self.profile.get('mqtt.port', 1883)
        self.username = self.profile.get('mqtt.username', '')
        self.password = self.profile.get('mqtt.password', None)
        self.reconnect_sec = self.profile.get('mqtt.reconnect_sec', 5)

        if self.profile.get('mqtt.enabled', False):
            self.transition('connecting')

    def in_started(self, message: Any, sender: ActorAddress) -> None:
        self.save_for_later(message, sender)

    def to_connecting(self, from_state:str) -> None:
        self.client = mqtt.Client()
        assert self.client is not None
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        if len(self.username) > 0:
            self._logger.debug('Logging in as %s' % self.username)
            self.client.username_pw_set(self.username, self.password)

        self._logger.debug('Connecting to MQTT broker %s:%s' % (self.host, self.port))
        self.client.connect_async(self.host, self.port)
        self.client.loop_start()

    def in_connecting(self, message: Any, sender: ActorAddress) -> None:
        if isinstance(message, MqttConnected):
            self.connected = True
            self.transition('connected')
        else:
            self.save_for_later(message, sender)

    def to_connected(self, from_state:str) -> None:
        assert self.client is not None
        # Subscribe to topics
        for topic in self.subscriptions:
            self.client.subscribe(topic)
            self._logger.debug('Subscribed to %s' % topic)

        # Publish outstanding messages
        for topic, payloads in self.publications.items():
            for payload in payloads:
                self.client.publish(topic, payload)

        self.publications.clear()

    def in_connected(self, message: Any, sender: ActorAddress) -> None:
        if isinstance(message, MqttDisconnected):
            if self.reconnect_sec > 0:
                self._logger.debug('Reconnecting in %s second(s)' % self.reconnect_sec)
                time.sleep(self.reconnect_sec)
                self.transition('started')
            else:
                self.transition('connecting')
        elif isinstance(message, MqttMessage):
            for receiver in self.subscriptions[message.topic]:
                self.send(receiver, message)
        elif self.connected:
            assert self.client is not None
            if isinstance(message, MqttSubscribe):
                receiver = message.receiver or sender
                self.subscriptions[message.topic].append(receiver)
                self.client.subscribe(message.topic)
                self._logger.debug('Subscribed to %s' % message.topic)
            elif isinstance(message, MqttPublish):
                self.client.publish(message.topic, message.payload)
        else:
            self.save_for_later(message, sender)

    def to_stopped(self, from_state:str) -> None:
        if self.client is not None:
            self.connected = False
            self._logger.debug('Stopping MQTT client')
            self.client.loop_stop()
            self.client = None

    # -------------------------------------------------------------------------

    def save_for_later(self, message: Any, sender: ActorAddress) -> None:
        if isinstance(message, MqttSubscribe):
            receiver = message.receiver or sender
            self.subscriptions[message.topic].append(receiver)
        elif isinstance(message, MqttPublish):
            self.publications[message.topic].append(message.payload)

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        self._logger.info('Connected to %s:%s' % (self.host, self.port))
        self.send(self.myAddress, MqttConnected())

    def on_disconnect(self, client, userdata, flags, rc):
        self._logger.warn('Disconnected')
        self.connected = False
        self.send(self.myAddress, MqttDisconnected())

    def on_message(self, client, userdata, msg):
        self.send(self.myAddress, MqttMessage(msg.topic, msg.payload))


        # try:
        #     if msg.topic == self.topic_hotword_detected:
        #         # hermes/hotword/<WAKEWORD_ID>/detected
        #         payload = json.loads(msg.payload.decode())
        #         if payload.get('siteId', '') != self.site_id:
        #             return

        #         logger.debug('Hotword detected!')
        #         keyphrase = payload.get('modelId', '')
        #         self.core.get_audio_recorder().stop_recording(False, True)

        #         # Handle in separate thread to avoid blocking.
        #         # I'll get around to using asyncio one of these days.
        #         threading.Thread(target=self.core._handle_wake,
        #                          args=(self.core.default_profile_name, keyphrase),
        #                          daemon=True).start()
        #     elif msg.topic == self.topic_hotword_on:
        #         # hermes/hotword/toggleOn
        #         payload = json.loads(msg.payload.decode())
        #         if payload.get('siteId', '') != self.site_id:
        #             return

        #         logger.debug('Hotword on')
        #         self.core.get_wake_listener(self.core.default_profile_name).start_listening()
        #     elif msg.topic == self.topic_hotword_off:
        #         # hermes/hotword/toggleOff
        #         payload = json.loads(msg.payload.decode())
        #         if payload.get('siteId', '') != self.site_id:
        #             return

        #         logger.debug('Hotword off')
        #         self.core.get_wake_listener(self.core.default_profile_name).stop_listening()
        #     elif msg.topic == self.topic_nlu_query:
        #         # hermes/nlu/query
        #         payload = json.loads(msg.payload.decode())
        #         logger.debug('NLU query')

        #         # Recognize intent
        #         text = payload['input']
        #         intent = self.core.get_intent_recognizer(self.core.default_profile_name).recognize(text)

        #         logger.debug(intent)

        #         # Publish intent
        #         # hermes/intent/<intentName>
        #         session_id = payload.get('sessionId', '')
        #         intent_payload = {
        #             'sessionId': session_id,
        #             'siteId': self.site_id,
        #             'input': text,
        #             'intent': {
        #                 'intentName': intent['intent']['name'],
        #                 'probability': intent['intent']['confidence']
        #             },
        #             'slots': [
        #                 { 'slotName': e['entity'],
        #                   'entity': e['entity'],
        #                   'value': e['value'],
        #                   'confidence': 1.0,
        #                   'raw_value': e['value'] }
        #                 for e in intent['entities']
        #             ]
        #         }

        #         topic = 'hermes/intent/%s' % intent['intent']['name']
        #         self.client.publish(topic, json.dumps(intent_payload).encode())

        #         # Handle intent
        #         self.core.get_intent_handler(self.core.default_profile_name).handle_intent(intent)
        #     elif msg.topic == self.topic_audio_frame:
        #         # hermes/audioServer/<SITE_ID>/audioFrame
        #         if self.on_audio_frame is not None:
        #             # Extract audio data
        #             with io.BytesIO(msg.payload) as wav_buffer:
        #                 with wave.open(wav_buffer, mode='rb') as wav_file:
        #                     audio_data = wav_file.readframes(wav_file.getnframes())
        #                     self.on_audio_frame(audio_data)

        # except Exception as e:
        #     logger.exception('on_message')

    # -------------------------------------------------------------------------

    # def text_captured(self, text, likelihood=0, seconds=0):
    #     if self.client is None:
    #         return

    #     payload = json.dumps({
    #         'siteId': self.site_id,
    #         'text': text,
    #         'likelihood': likelihood,
    #         'seconds': seconds
    #     }).encode()

    #     topic = 'hermes/asr/textCaptured'
    #     self.client.publish(topic, payload)