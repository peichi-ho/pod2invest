from django.urls import path
from .views import chat, conversation_list, conversation_messages, delete_conversation

urlpatterns = [
    path("chat/", chat, name="ai_assistant_chat"),
    path("conversations/", conversation_list, name="ai_assistant_conversations"),
    path("conversations/<int:conversation_id>/messages/", conversation_messages, name="ai_assistant_messages"),
    path("conversations/<int:conversation_id>/", delete_conversation, name="ai_assistant_delete_conversation"),
]
