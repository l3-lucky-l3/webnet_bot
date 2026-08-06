from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0055_promocode_max_uses_per_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='antilopay_order_id',
            field=models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='Order ID Antilopay', help_text='Уникальный идентификатор заказа для вебхуков'),
        ),
    ]
