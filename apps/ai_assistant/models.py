from django.db import models


class Conversation(models.Model):
    """一個聊天視窗對應一筆 Conversation"""
    title = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ai_assistant"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"Conversation {self.pk}"


class Message(models.Model):
    """Conversation 內的每則訊息"""
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ai_assistant"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}"
