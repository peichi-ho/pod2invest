import re

from django.core.exceptions import ValidationError


class LetterDigitPasswordValidator:
    """
    密碼規則：至少 min_length 個字元，而且英文字母和數字都要有。
    取代 Django 預設那組（常見密碼清單會把 e3r4t5y6 這類鍵盤排列擋掉，太嚴格），
    錯誤訊息直接用中文，註冊頁跟改密碼 API 共用。
    """

    def __init__(self, min_length=8):
        self.min_length = min_length

    def validate(self, password, user=None):
        if len(password) < self.min_length:
            raise ValidationError(f'密碼至少需要 {self.min_length} 個字元', code='password_too_short')
        if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
            raise ValidationError('密碼必須同時包含英文字母和數字', code='password_no_letter_digit')

    def get_help_text(self):
        return f'至少 {self.min_length} 個字元，需同時包含英文字母和數字'
