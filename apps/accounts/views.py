from django.shortcuts import render, redirect
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from rest_framework.authentication import SessionAuthentication
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import UserProfile, FavoriteAsset, FavoriteEpisode, FavoritePodcast, WatchHistory


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
    authentication_classes = [CsrfExemptSessionAuthentication]

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

    def post(self, request):
        """
        個人資料頁「編輯偏好」用：重新填寫跟 onboarding 問卷完全一樣的 5 個欄位並存檔
        （level/markets/style/goal/capital）。刻意跟 onboarding_view 用同一套欄位，
        因為首頁 discover.js 的 _prefScore() 就是讀這幾個欄位來排序推薦內容，欄位對不
        起來的話「編輯偏好後首頁推薦跟著變」就不會生效。
        """
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        markets = request.data.get('markets')
        if markets is None:
            markets = []
        if not isinstance(markets, list):
            return Response({'error': 'markets 必須是陣列'}, status=status.HTTP_400_BAD_REQUEST)
        if not markets:
            markets = ['General']

        fields = dict(
            level=(request.data.get('level') or '').strip(),
            markets=markets,
            style=(request.data.get('style') or '').strip(),
            goal=(request.data.get('goal') or '').strip(),
            capital=(request.data.get('capital') or '').strip(),
        )

        profile = UserProfile.objects.using('accountsdb').filter(user_id=request.user.id).first()
        if profile:
            for k, v in fields.items():
                setattr(profile, k, v)
            profile.onboarding_done = True
            profile.save(using='accountsdb')
        else:
            profile = UserProfile.objects.using('accountsdb').create(
                user_id=request.user.id, onboarding_done=True, **fields
            )

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


class FavoriteEpisodesAPIView(APIView):
    """使用者收藏的單集清單（Saved Insights）。只回傳 summary_id，顯示用的標題/
    節目名稱/日期由前端另外打 /api/summaries/?ids=... 取得，跟資產收藏
    （FavoritesAPIView 只回傳 symbol，細節另外查）的既有慣例一致。"""
    authentication_classes = [CsrfExemptSessionAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        rows = FavoriteEpisode.objects.using('accountsdb').filter(user_id=request.user.id).order_by('-created_at')
        return Response({'summary_ids': [r.summary_id for r in rows]})


class FavoriteEpisodeToggleAPIView(APIView):
    authentication_classes = [CsrfExemptSessionAuthentication]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            summary_id = int(request.data.get('summary_id'))
        except (TypeError, ValueError):
            return Response({'error': 'summary_id 必填'}, status=status.HTTP_400_BAD_REQUEST)

        existing = FavoriteEpisode.objects.using('accountsdb').filter(
            user_id=request.user.id, summary_id=summary_id
        ).first()
        if existing:
            existing.delete(using='accountsdb')
            return Response({'favorited': False})

        FavoriteEpisode.objects.using('accountsdb').create(user_id=request.user.id, summary_id=summary_id)
        return Response({'favorited': True})


class FavoritePodcastsAPIView(APIView):
    """使用者收藏（追蹤）的節目清單（最愛 Podcast）。只回傳 podcaster 名稱，集數/
    準確率等顯示用資料由前端另外打現有的 /api/summaries/podcasters/ 等 API 取得。"""
    authentication_classes = [CsrfExemptSessionAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        rows = FavoritePodcast.objects.using('accountsdb').filter(user_id=request.user.id).order_by('-created_at')
        return Response({'podcasters': [r.podcaster for r in rows]})


class FavoritePodcastToggleAPIView(APIView):
    authentication_classes = [CsrfExemptSessionAuthentication]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        podcaster = (request.data.get('podcaster') or '').strip()
        if not podcaster:
            return Response({'error': 'podcaster 必填'}, status=status.HTTP_400_BAD_REQUEST)

        existing = FavoritePodcast.objects.using('accountsdb').filter(
            user_id=request.user.id, podcaster=podcaster
        ).first()
        if existing:
            existing.delete(using='accountsdb')
            return Response({'favorited': False})

        FavoritePodcast.objects.using('accountsdb').create(user_id=request.user.id, podcaster=podcaster)
        return Response({'favorited': True})


class WatchHistoryAPIView(APIView):
    """使用者的單集觀看紀錄。GET 給 Profile 頁顯示（?limit= 取前 N 筆，不帶則全部，
    給「顯示全部」彈窗用）；POST 由 deep_dive.js 在打開單集頁面時自動呼叫，
    upsert 一筆（同一集重複打開只更新 viewed_at，不會累加重複項目）。"""
    authentication_classes = [CsrfExemptSessionAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        qs = WatchHistory.objects.using('accountsdb').filter(user_id=request.user.id).order_by('-viewed_at')
        limit = request.query_params.get('limit')
        if limit:
            try:
                qs = qs[:int(limit)]
            except ValueError:
                pass
        return Response({'items': [
            {'summary_id': r.summary_id, 'viewed_at': r.viewed_at.isoformat()} for r in qs
        ]})

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            summary_id = int(request.data.get('summary_id'))
        except (TypeError, ValueError):
            return Response({'error': 'summary_id 必填'}, status=status.HTTP_400_BAD_REQUEST)

        row, created = WatchHistory.objects.using('accountsdb').get_or_create(
            user_id=request.user.id, summary_id=summary_id
        )
        if not created:
            row.save(using='accountsdb')  # viewed_at 是 auto_now，save() 就會刷新成現在時間
        return Response({'ok': True})


class ProfileAPIView(APIView):
    """Profile 頁最上方資訊（使用者名稱／Email／頭像／加入年月）＋ Edit Profile 的
    姓名／Email 編輯。密碼變更需要先驗證原密碼，另外走 ChangePasswordAPIView。"""
    authentication_classes = [CsrfExemptSessionAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        user = request.user
        profile = UserProfile.objects.using('accountsdb').filter(user_id=user.id).first()
        return Response({
            'username': user.username,
            'email': user.email,
            'avatar_base64': profile.avatar_base64 if profile else '',
            'date_joined': user.date_joined.isoformat(),
        })

    def patch(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        user = request.user

        username = request.data.get('username')
        if username is not None:
            username = username.strip()
            if not username:
                return Response({'error': '姓名不能為空'}, status=status.HTTP_400_BAD_REQUEST)
            if User.objects.exclude(pk=user.pk).filter(username=username).exists():
                return Response({'error': '這個名稱已經有人使用了'}, status=status.HTTP_400_BAD_REQUEST)
            user.username = username

        email = request.data.get('email')
        if email is not None:
            user.email = email.strip()

        user.save()
        return Response({'username': user.username, 'email': user.email})


class AvatarAPIView(APIView):
    """Profile 頁大頭貼上傳（點頭像圓框觸發）。前端已先把檔案限制在 ~2MB 內再轉
    base64，這裡只做寬鬆的格式／大小防呆。傳空字串等於清掉大頭貼、改回預設圖示。"""
    authentication_classes = [CsrfExemptSessionAuthentication]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        avatar_base64 = request.data.get('avatar_base64', '') or ''
        if avatar_base64 and not avatar_base64.startswith('data:image/'):
            return Response({'error': '圖片格式錯誤'}, status=status.HTTP_400_BAD_REQUEST)
        if len(avatar_base64) > 4 * 1024 * 1024:
            return Response({'error': '圖片太大'}, status=status.HTTP_400_BAD_REQUEST)

        profile = UserProfile.objects.using('accountsdb').filter(user_id=request.user.id).first()
        if profile:
            profile.avatar_base64 = avatar_base64
            profile.save(using='accountsdb')
        else:
            profile = UserProfile.objects.using('accountsdb').create(
                user_id=request.user.id, avatar_base64=avatar_base64
            )
        return Response({'avatar_base64': profile.avatar_base64})


class ChangePasswordAPIView(APIView):
    authentication_classes = [CsrfExemptSessionAuthentication]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)
        user = request.user
        old_password = request.data.get('old_password') or ''
        new_password = request.data.get('new_password') or ''

        if not user.check_password(old_password):
            return Response({'error': '原密碼不正確'}, status=status.HTTP_400_BAD_REQUEST)
        if len(new_password) < 8:
            return Response({'error': '新密碼至少需要 8 個字元'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()
        update_session_auth_hash(request, user)  # 改密碼後維持目前 session 登入狀態，不強制登出
        return Response({'ok': True})
