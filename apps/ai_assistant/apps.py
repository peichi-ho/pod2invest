import os
import sys
import threading
from django.apps import AppConfig

_SKIP_CMDS = {'migrate', 'makemigrations', 'collectstatic', 'test', 'shell', 'createsuperuser'}


class AiAssistantConfig(AppConfig):
    name = 'apps.ai_assistant'

    def ready(self):
        if set(sys.argv) & _SKIP_CMDS:
            return
        if os.environ.get('RUN_MAIN') != 'true':
            return
        threading.Thread(target=self._preload_models, daemon=True).start()

    @staticmethod
    def _preload_models():
        try:
            from apps.ai_assistant.services.ai_assistant import _get_embed_model, _get_reranker_model
            _get_embed_model()
            _get_reranker_model()
        except Exception:
            pass
