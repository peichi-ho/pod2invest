from django.db import models


class Conversation(models.Model):
    """一個聊天視窗對應一筆 Conversation"""
    # 對話擁有者。User 在 default 資料庫、這張表在 ai_assistant_db，跨資料庫不能用
    # ForeignKey，比照 apps/accounts 的做法存一般整數欄位。null=True：資料庫是組員共用的，
    # 加欄位前建立的舊對話沒有擁有者（API 一律不回傳），還沒更新程式的人寫入也不會出錯。
    user_id = models.IntegerField(null=True, blank=True, db_index=True)
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
