from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import UserProfile


@login_required(login_url='/login/')
def onboarding_view(request):
    # 已填過問卷直接進首頁
    profile = UserProfile.objects.using('accountsdb').filter(user_id=request.user.id).first()
    if profile and profile.onboarding_done:
        return redirect('/')

    if request.method == 'POST':
        markets = request.POST.getlist('markets')
        if not markets:
            markets = ['General']

        if profile:
            profile.level = request.POST.get('level', '')
            profile.markets = markets
            profile.style = request.POST.get('style', '')
            profile.goal = request.POST.get('goal', '')
            profile.capital = request.POST.get('capital', '')
            profile.onboarding_done = True
            profile.save(using='accountsdb')
        else:
            UserProfile.objects.using('accountsdb').create(
                user_id=request.user.id,
                level=request.POST.get('level', ''),
                markets=markets,
                style=request.POST.get('style', ''),
                goal=request.POST.get('goal', ''),
                capital=request.POST.get('capital', ''),
                onboarding_done=True,
            )
        return redirect('/')

    return render(request, 'accounts/onboarding.html')


class PreferencesAPIView(APIView):
    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        profile = UserProfile.objects.using('accountsdb').filter(user_id=request.user.id).first()
        if not profile or not profile.onboarding_done:
            return Response({})
        return Response({
            'level': profile.level,
            'markets': profile.markets,
            'style': profile.style,
            'goal': profile.goal,
            'capital': profile.capital,
        })
