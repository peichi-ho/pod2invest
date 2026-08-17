from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from rest_framework.authentication import SessionAuthentication
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import UserProfile, FavoriteAsset


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    DRF 的 SessionAuthentication 在使用者是透過 session 登入時，預設會額外強制驗證
    CSRF token；但這個專案的前端 fetch() 從來沒有帶過 X-CSRFToken（比照
    apps/ai_assistant/views.py 的 chat() 用 @csrf_exempt 處理 POST 的既有慣例）。
    這裡覆寫掉 enforce_csrf 讓它直接放行，效果等同 @csrf_exempt，同時維持
    request.user 正常從 session 解析出來。
    """
    def enforce_csrf(self, request):
        return


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


class FavoritesAPIView(APIView):
    authentication_classes = [CsrfExemptSessionAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        rows = FavoriteAsset.objects.using('accountsdb').filter(user_id=request.user.id)
        return Response({'symbols': [{'category': r.category, 'symbol': r.symbol} for r in rows]})


class FavoriteToggleAPIView(APIView):
    authentication_classes = [CsrfExemptSessionAuthentication]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        category = (request.data.get('category') or '').strip()
        symbol = (request.data.get('symbol') or '').strip().upper()
        if not category or not symbol:
            return Response({'error': 'category/symbol 必填'}, status=status.HTTP_400_BAD_REQUEST)

        existing = FavoriteAsset.objects.using('accountsdb').filter(
            user_id=request.user.id, category=category, symbol=symbol
        ).first()
        if existing:
            existing.delete(using='accountsdb')
            return Response({'favorited': False})

        FavoriteAsset.objects.using('accountsdb').create(
            user_id=request.user.id, category=category, symbol=symbol
        )
        return Response({'favorited': True})
