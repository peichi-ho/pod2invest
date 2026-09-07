"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from apps.accounts.views import onboarding_view
from apps.accounts.models import UserProfile

# 訪客可以直接瀏覽首頁（探索、排行榜、搜尋都不擋），只有打開單集深度摘要時才會被要求
# 登入——見 static/js/deep_dive.js 的 openDeepDive()。其餘 API（summaries/podcasts/...）
# 本來就沒有做登入檢查，只有 apps/accounts 這幾支會自己判斷 request.user.is_authenticated，
# 所以拿掉這裡的 @login_required 不會讓其他功能對訪客壞掉。
def frontend_index(request):
    return render(request, 'index.html')

def login_view(request):
    if request.user.is_authenticated:
        return redirect('/')
    error = None
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect('/')
        error = '帳號或密碼錯誤，請再試一次。'
    return render(request, 'auth/login.html', {'error': error})

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('/')
    error = None
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            email = (request.POST.get('email') or '').strip()
            if email:
                user.email = email
                user.save()
            login(request, user)
            # 註冊時可以選擇性附上頭像（base64 data URI），沒選就先不建立 UserProfile，
            # 之後 onboarding_view 送出問卷時才會建立那筆——避免這裡跟 onboarding_view
            # 各自用不同邏輯 create 出兩筆重複的 UserProfile。
            avatar_base64 = (request.POST.get('avatar_base64') or '').strip()
            if avatar_base64:
                UserProfile.objects.using('accountsdb').create(user_id=user.id, avatar_base64=avatar_base64)
            return redirect('/onboarding/')
        error = list(form.errors.values())[0][0]
    return render(request, 'auth/signup.html', {'error': error})

def logout_view(request):
    if request.method == 'POST':
        logout(request)
    return redirect('/login/')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/',  login_view,  name='login'),
    path('signup/', signup_view, name='signup'),
    path('logout/', logout_view, name='logout'),
    path("api/glossary/", include("apps.glossary.urls")),
    path("api/summaries/", include("apps.summaries.urls")),
    path("api/mindmap/", include("apps.mindmap.urls")),
    path("api/etf/", include("apps.etf.api.urls")),
    path("api/ai/", include("apps.ai_assistant.urls")),
    path("api/knowledge-graph/", include("apps.knowledge_graph.urls")),
    path("api/calculator/", include("apps.calculator.urls")),
    path("api/accounts/", include("apps.accounts.urls")),
    path("api/assets/", include("apps.assets.api.urls")),
    path('onboarding/', onboarding_view, name='onboarding'),
    path('', frontend_index),
]