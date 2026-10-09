"""Audio recaps with Azure AI Speech (the Foundry resource's Speech endpoint, Entra auth)."""

from __future__ import annotations

import os
from xml.sax.saxutils import escape

import aiohttp

VOICES = {"en": ("en-US", "en-US-AvaMultilingualNeural"), "es": ("es-ES", "es-ES-ElviraNeural"),
          "ja": ("ja-JP", "ja-JP-NanamiNeural")}


class Speech:
    def __init__(self, credential, resource_id: str | None = None, region: str | None = None):
        self.cred = credential
        self.resource_id = resource_id or os.environ.get("SPEECH_RESOURCE_ID", "")
        self.region = region or os.environ.get("SPEECH_REGION", "eastus2")

    @property
    def enabled(self) -> bool:
        return bool(self.resource_id)

    async def synthesize(self, text: str, lang: str) -> bytes:
        locale, voice = VOICES.get(lang, VOICES["en"])
        token = (await self.cred.get_token("https://cognitiveservices.azure.com/.default")).token
        ssml = (f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>"
                f"<prosody rate='+5%'>{escape(text)}</prosody></voice></speak>")
        headers = {"Authorization": f"Bearer aad#{self.resource_id}#{token}",
                   "Content-Type": "application/ssml+xml",
                   "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "matchlens"}
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        async with aiohttp.ClientSession() as s, s.post(url, data=ssml.encode(), headers=headers,
                                                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            return await r.read()
