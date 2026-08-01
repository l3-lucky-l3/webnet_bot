from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

@csrf_exempt
@require_http_methods(["GET"])
def test_api(request):
    """Простой тест API"""
    return JsonResponse({
        'success': True,
        'message': 'API работает!'
    })
