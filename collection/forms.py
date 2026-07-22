"""密碼重設流程用的表單。

Django 內建的 PasswordResetForm / SetPasswordForm 使用未加樣式的原生 widget，
在 unfold 深色主題下輸入框文字會與背景幾乎同色而看不見。這裡沿用 unfold 登入表單
所使用的 BASE_INPUT_CLASSES，讓輸入框套用與登入頁一致的樣式（含深／淺色主題）。
不新增任何 CSS 框架或圖示套件，僅重用 unfold 既有的 class。
"""
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from unfold.widgets import BASE_INPUT_CLASSES

_INPUT_CLASS = " ".join(BASE_INPUT_CLASSES)


class UnfoldPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs["class"] = _INPUT_CLASS


class UnfoldSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].widget.attrs["class"] = _INPUT_CLASS
        self.fields["new_password2"].widget.attrs["class"] = _INPUT_CLASS
