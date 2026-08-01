from django.urls import path
from . import views, referral_views, referral_admin_views, balance_views, test_views, simple_payment_views, referral_withdrawal_views
from . import user_api, user_api_trial

app_name = 'bot_management'

urlpatterns = [
    # Главная страница
    path('', views.dashboard, name='dashboard'),

    # Health check endpoint
    path('api/health/', user_api.health_check, name='health_check'),

    # Кастомные страницы
    path('users/', views.users_list, name='users_list'),
    path('payments/', views.payments_list, name='payments_list'),
    path('keys/', views.keys_list, name='keys_list'),
    path('keys/add/', views.add_keys_page, name='add_keys'),
    path('keys/<int:key_id>/edit/', views.edit_key_page, name='edit_key'),
    path('keys/<int:key_id>/delete/', views.delete_key, name='delete_key'),
    
    # API для пользователей
    path('api/users/<int:user_id>/keys/', views.get_user_keys, name='get_user_keys'),
    
    # Webhook для бота
    path('webhook/', views.BotWebhookView.as_view(), name='webhook'),
    
    # Webhook для ЮKassa
    path('webhook/yookassa/', views.yookassa_webhook, name='yookassa_webhook'),
    # Webhook для Platega
    path('webhook/platega/', views.platega_webhook, name='platega_webhook'),
    # Webhook для CryptoBot
    path('webhook/cryptobot/', views.cryptobot_webhook, name='cryptobot_webhook'),
    # Webhook для Antilopay
    path('webhook/antilopay/', views.antilopay_webhook, name='antilopay_webhook'),
    path('webhook/test/', views.test_webhook, name='test_webhook'),
    
    # Упрощенные API для платежей
    path('api/simple/payment/create/', simple_payment_views.create_simple_payment, name='create_simple_payment'),
    path('api/simple/payment/<str:payment_id>/status/', simple_payment_views.check_simple_payment_status, name='check_simple_payment_status'),
    path('api/simple/webhook/', simple_payment_views.process_payment_webhook, name='process_payment_webhook'),
    
    # Payment management URLs
    path('api/payments/auto-capture/toggle/', views.toggle_auto_capture, name='toggle_auto_capture'),
    path('api/payments/auto-capture/status/', views.get_auto_capture_status, name='get_auto_capture_status'),
    path('api/payments/<int:payment_id>/manual-capture/', views.manual_capture_payment, name='manual_capture_payment'),
    
    # API (должны быть выше других URL с параметрами)
    path('api/statistics/', views.statistics_api, name='statistics_api'),
    path('api/payments/create/', views.create_payment, name='create_payment'),
    path('api/payments/create-cryptobot/', views.create_cryptobot_payment, name='create_cryptobot_payment'),
    path('api/payments/create-antilopay/', views.create_antilopay_payment, name='create_antilopay_payment'),
    path('api/payments/<int:payment_id>/status/', views.get_payment_status, name='get_payment_status'),
    path('api/payments/<int:payment_id>/platega-status/', views.check_platega_payment_status, name='check_platega_payment_status'),
    path('api/payments/<int:payment_id>/cryptobot-status/', views.check_cryptobot_payment_status, name='check_cryptobot_payment_status'),
    path('api/payments/<int:payment_id>/antilopay-status/', views.check_antilopay_payment_status, name='check_antilopay_payment_status'),
    path('api/platega/transaction/<str:transaction_id>/check/', views.check_platega_payment_by_transaction_id, name='check_platega_payment_by_transaction_id'),
    path('api/payments/<str:payment_id>/yookassa-status/', views.get_yookassa_payment_status, name='get_yookassa_payment_status'),
    path('api/payments/<int:payment_id>/confirm/', views.manual_confirm_payment, name='manual_confirm_payment'),
    path('api/test-support/', views.test_support_send, name='test_support_send'),
    path('api/simple-test/', views.simple_test, name='simple_test'),
    path('api/test-support-simple/', views.test_support_simple, name='test_support_simple'),
    path('send-support/', views.send_support_page, name='send_support_page'),
    
    # Действия с платежами
    path('payments/<int:payment_id>/<str:action>/', views.payment_actions, name='payment_actions'),
    
    # Рассылки
    path('broadcast/create/', views.broadcast_create, name='broadcast_create'),
    
    # Поддержка
    path('support/send-message/', views.send_support_message, name='send_support_message'),
    path('api/support/send/', views.send_support_message, name='api_send_support_message'),
    path('api/support/receive/', views.receive_support_message, name='api_receive_support_message'),
    path('support/', views.support_chat_list, name='support_chat_list'),
    path('support/<int:chat_id>/', views.support_chat_detail, name='support_chat_detail'),
    path('support/<int:chat_id>/reply/', views.support_reply, name='support_reply'),
    path('support/<int:chat_id>/delete/', views.delete_support_chat, name='delete_support_chat'),
    path('support/create/', views.create_support_chat, name='create_support_chat'),
    path('support/<int:chat_id>/toggle/', views.toggle_support_chat, name='toggle_support_chat'),
    
    # Тест API
    path('api/test/', test_views.test_api, name='test_api'),
    
    # API для рефералов
    path('api/referral/create/', referral_views.create_referral_code, name='create_referral_code'),
    path('api/referral/process/', referral_views.process_referral, name='process_referral'),
    path('api/referral/stats/<int:user_id>/', referral_views.get_referral_stats, name='get_referral_stats'),
    path('api/referral/purchase/', referral_views.process_referral_purchase, name='process_referral_purchase'),
    
    # API для пользователей
    path('api/user/set-entry-method/', views.set_user_entry_method, name='set_user_entry_method'),
    path('api/user/<int:user_id>/trial_status/', user_api_trial.get_user_trial_status, name='get_user_trial_status'),
    path('api/user/<int:user_id>/issue_trial_key/', user_api_trial.issue_trial_key, name='issue_trial_key'),
    path('api/subscription/<int:payment_id>/reset-devices/', user_api.reset_subscription_devices, name='reset_subscription_devices'),
    path('api/subscription/<int:payment_id>/devices/', user_api.get_subscription_devices, name='get_subscription_devices'),
    path('api/subscription/<int:payment_id>/delete-device/', user_api.delete_subscription_device, name='delete_subscription_device'),

    # API статистики платежей
    path('api/payments/stats/today/', user_api.get_payment_stats_today, name='get_payment_stats_today'),
    
    # API для баланса и профиля
    path('api/users/<int:user_id>/profile/', balance_views.get_user_profile, name='get_user_profile'),
    path('api/balance/deposit/', balance_views.create_balance_deposit, name='create_balance_deposit'),
    path('api/balance/process-payment/', balance_views.process_balance_payment, name='process_balance_payment'),
    path('api/balance/payment-status/<int:payment_id>/', balance_views.get_balance_payment_status, name='get_balance_payment_status'),
    path('api/balance/payment-confirm/<int:payment_id>/', balance_views.manual_confirm_balance_payment, name='manual_confirm_balance_payment'),
    path('api/balance/refund/', balance_views.refund_balance, name='refund_balance'),
    path('api/subscription/buy-with-balance/', balance_views.buy_subscription_with_balance, name='buy_subscription_with_balance'),
    
    # Управление рефералами
    path('referral/', referral_admin_views.referral_management, name='referral_management'),
    path('referral/user/<int:user_id>/', referral_admin_views.referral_details, name='referral_details'),
    path('referral/toggle/<int:user_id>/', referral_admin_views.toggle_referral_code, name='toggle_referral_code'),
    path('referral/pay/<int:reward_id>/', referral_admin_views.pay_reward, name='pay_reward'),
    
    # API для реферальных выводов
    path('api/referral/withdrawal/request/', referral_withdrawal_views.request_withdrawal, name='request_withdrawal'),
    path('api/referral/withdrawal/status/<int:user_id>/', referral_withdrawal_views.get_withdrawal_status, name='get_withdrawal_status'),
    path('api/referral/balance/<int:user_id>/', referral_withdrawal_views.get_referral_balance, name='get_referral_balance'),
    path('api/referral/payment/', referral_withdrawal_views.pay_with_referral_balance, name='pay_with_referral_balance'),
    path('api/withdrawal/notification/', views.withdrawal_notification_api, name='withdrawal_notification_api'),
    
    # Админ панель для выводов
    path('withdrawals/', referral_withdrawal_views.withdrawal_management, name='withdrawal_management'),
    path('withdrawals/<int:withdrawal_id>/process/', referral_withdrawal_views.process_withdrawal, name='process_withdrawal'),
    
    # API для управления ключами и ценами
    path('api/keys/upload/', views.upload_keys_api, name='upload_keys_api'),
    path('api/keys/list/', views.get_keys_list_api, name='get_keys_list_api'),
    path('api/keys/toggle/', views.toggle_key_api, name='toggle_key_api'),
    path('api/keys/delete/', views.delete_key_api, name='delete_key_api'),
    path('api/keys/<int:key_id>/detail/', views.get_key_detail_api, name='get_key_detail_api'),
    path('api/prices/update/', views.update_price_api, name='update_price_api'),
    path('api/prices/get/', views.get_prices_api, name='get_prices_api'),
    path('api/subscription/name/update/', views.update_subscription_name_api, name='update_subscription_name_api'),
    
    # API для списков
    path('api/payments/list/', views.get_payments_list_api, name='get_payments_list_api'),
    path('api/payments/<int:payment_id>/detail/', views.get_payment_detail_api, name='get_payment_detail_api'),
    path('api/payments/confirm/', views.confirm_payment_api, name='confirm_payment_api'),
    path('api/users/list/', views.get_users_list_api, name='get_users_list_api'),
    
    # API для настроек
    path('api/settings/get/', views.get_setting_api, name='get_setting_api'),
    path('api/settings/update/', views.update_setting_api, name='update_setting_api'),
    
    # API для рефереров
    path('api/referrers/list/', views.get_referrers_list_api, name='get_referrers_list_api'),
    path('api/referrers/<int:user_id>/detail/', views.get_referrer_detail_api, name='get_referrer_detail_api'),
    path('api/referrers/<int:user_id>/export/', views.export_referrer_referrals_api, name='export_referrer_referrals_api'),
    path('api/referrers/export/', views.export_referrers_api, name='export_referrers_api'),

    # API статуса планировщика
    path('api/promo/validate/', views.validate_promo_code, name='validate_promo_code'),
    path('api/scheduler_status/', views.scheduler_status_api, name='scheduler_status_api'),
]