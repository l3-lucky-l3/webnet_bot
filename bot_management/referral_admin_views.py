from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Count, Sum, Q
from django.utils import timezone
from datetime import timedelta
from .models import TelegramUser, ReferralCode, Referral, ReferralReward, ReferralWithdrawal
from .referral_services import ReferralService

def staff_required(view_func):
    """Декоратор для проверки прав администратора"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect('admin:login')
        return view_func(request, *args, **kwargs)
    return wrapper

@staff_required
def referral_management(request):
    """Управление реферальной системой"""
    # Статистика рефералов
    total_referral_codes = ReferralCode.objects.count()
    active_referral_codes = ReferralCode.objects.filter(is_active=True).count()
    total_referrals = Referral.objects.count()
    active_referrals = Referral.objects.filter(is_active=True).count()
    
    # Общая сумма наград
    total_rewards = ReferralReward.objects.filter(status='paid').aggregate(
        total=Sum('reward_value')
    )['total'] or 0
    
    # Средняя награда
    paid_rewards_count = ReferralReward.objects.filter(status='paid').count()
    if paid_rewards_count > 0:
        average_reward = total_rewards / paid_rewards_count
    else:
        average_reward = 0
    
    # Топ рефереров
    top_referrers = TelegramUser.objects.annotate(
        referrals_count=Count('referrals_made', filter=Q(referrals_made__is_active=True)),
        total_rewards=Sum('referrals_made__rewards__reward_value', 
                         filter=Q(referrals_made__rewards__status='paid'))
    ).filter(referrals_count__gt=0).order_by('-referrals_count')[:10]
    
    # Недавние рефералы
    recent_referrals = Referral.objects.select_related(
        'referrer', 'referred'
    ).order_by('-created_at')[:10]
    
    # Недавние награды
    recent_rewards = ReferralReward.objects.select_related(
        'referral__referrer', 'referral__referred'
    ).order_by('-created_at')[:10]
    
    # Статистика за последние 30 дней
    thirty_days_ago = timezone.now() - timedelta(days=30)
    
    # Новые рефералы за 30 дней
    recent_referrals_30_days = Referral.objects.filter(
        created_at__gte=thirty_days_ago
    ).count()
    
    # Выплаченные награды за 30 дней
    recent_rewards_30_days = ReferralReward.objects.filter(
        status='paid',
        created_at__gte=thirty_days_ago
    ).aggregate(
        total=Sum('reward_value')
    )['total'] or 0
    
    # Ожидающие выплаты награды
    pending_rewards = ReferralReward.objects.filter(status='pending').count()
    
    # Общий реферальный баланс всех пользователей
    total_referral_balance = TelegramUser.objects.aggregate(
        total=Sum('referral_balance')
    )['total'] or 0
    
    # Количество пользователей с реферальным балансом
    users_with_referral_balance = TelegramUser.objects.filter(
        referral_balance__gt=0
    ).count()
    
    # Статистика заявок на вывод
    total_withdrawals = ReferralWithdrawal.objects.count()
    pending_withdrawals = ReferralWithdrawal.objects.filter(status='pending').count()
    approved_withdrawals = ReferralWithdrawal.objects.filter(status='approved').count()
    completed_withdrawals = ReferralWithdrawal.objects.filter(status='completed').count()
    rejected_withdrawals = ReferralWithdrawal.objects.filter(status='rejected').count()
    
    # Общая сумма заявок на вывод
    total_withdrawal_amount = ReferralWithdrawal.objects.aggregate(
        total=Sum('amount')
    )['total'] or 0
    
    # Недавние заявки на вывод
    recent_withdrawals = ReferralWithdrawal.objects.select_related(
        'user'
    ).order_by('-created_at')[:5]
    
    context = {
        'total_referral_codes': total_referral_codes,
        'active_referral_codes': active_referral_codes,
        'total_referrals': total_referrals,
        'active_referrals': active_referrals,
        'total_rewards': total_rewards,
        'average_reward': average_reward,
        'top_referrers': top_referrers,
        'recent_referrals': recent_referrals,
        'recent_rewards': recent_rewards,
        'recent_referrals_30_days': recent_referrals_30_days,
        'recent_rewards_30_days': recent_rewards_30_days,
        'pending_rewards': pending_rewards,
        'total_referral_balance': total_referral_balance,
        'users_with_referral_balance': users_with_referral_balance,
        'total_withdrawals': total_withdrawals,
        'pending_withdrawals': pending_withdrawals,
        'approved_withdrawals': approved_withdrawals,
        'completed_withdrawals': completed_withdrawals,
        'rejected_withdrawals': rejected_withdrawals,
        'total_withdrawal_amount': total_withdrawal_amount,
        'recent_withdrawals': recent_withdrawals,
    }
    
    return render(request, 'bot_management/referral_management.html', context)

@staff_required
def referral_details(request, user_id):
    """Детали реферала"""
    user = get_object_or_404(TelegramUser, user_id=user_id)
    
    # Реферальный код пользователя
    try:
        referral_code = ReferralCode.objects.get(user=user)
    except ReferralCode.DoesNotExist:
        referral_code = None
    
    # Рефералы пользователя
    referrals = Referral.objects.filter(referrer=user).select_related('referred')
    
    # Награды пользователя
    rewards = ReferralReward.objects.filter(
        referral__referrer=user
    ).select_related('referral__referred')
    
    # Статистика
    total_referrals = referrals.count()
    active_referrals = referrals.filter(is_active=True).count()
    total_rewards = rewards.filter(status='paid').aggregate(
        total=Sum('reward_value')
    )['total'] or 0
    
    context = {
        'user': user,
        'referral_code': referral_code,
        'referrals': referrals,
        'rewards': rewards,
        'total_referrals': total_referrals,
        'active_referrals': active_referrals,
        'total_rewards': total_rewards,
    }
    
    return render(request, 'bot_management/referral_details.html', context)

@staff_required
def toggle_referral_code(request, user_id):
    """Включить/выключить реферальный код"""
    if request.method == 'POST':
        try:
            user = get_object_or_404(TelegramUser, user_id=user_id)
            referral_code = get_object_or_404(ReferralCode, user=user)
            referral_code.is_active = not referral_code.is_active
            referral_code.save()
            
            return JsonResponse({
                'success': True,
                'is_active': referral_code.is_active,
                'message': f'Реферальный код {"активирован" if referral_code.is_active else "деактивирован"}'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'message': 'Неверный метод запроса'})

@staff_required
def pay_reward(request, reward_id):
    """Выплатить награду"""
    if request.method == 'POST':
        try:
            reward = get_object_or_404(ReferralReward, id=reward_id)
            reward.status = 'paid'
            reward.paid_at = timezone.now()
            reward.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Награда {reward.reward_value} ₽ выплачена'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'message': 'Неверный метод запроса'})
