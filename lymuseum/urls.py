"""
URL configuration for lymuseum project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from collection.forms import UnfoldPasswordResetForm, UnfoldSetPasswordForm

# 後台「忘記密碼」密碼重設流程（請求→寄信→設定新密碼→完成）。
# 放在 admin/ 之前，讓 admin/password_reset/ 不被 admin 攔截；註冊
# admin_password_reset 名稱後，unfold 登入頁的「忘記密碼」連結會自動出現。
password_reset_patterns = [
    path(
        'admin/password_reset/',
        auth_views.PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt',
            form_class=UnfoldPasswordResetForm,
            success_url=reverse_lazy('password_reset_done'),
            # 供模板判斷是否已設定寄信服務（未設定時顯示提示、不無聲失敗）
            extra_context={'email_configured': bool(settings.EMAIL_HOST)},
        ),
        name='admin_password_reset',
    ),
    path(
        'admin/password_reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            form_class=UnfoldSetPasswordForm,
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
]

urlpatterns = password_reset_patterns + [
    path('admin/', admin.site.urls),
    path('', include('collection.urls')),
]

# 開發模式下由 runserver 提供使用者上傳的媒體檔（標本影像等）
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
