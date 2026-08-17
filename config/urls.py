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
from django.contrib.auth.decorators import login_required
from apps.accounts.views import onboarding_view

@login_required(login_url='/login/')
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
            login(request, user)
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