# Generated manually for hourly subscription reminder fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0049_add_subscription_reminder_1d_sent'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='subscription_reminder_5h_sent',
            field=models.BooleanField(db_index=True, default=False, help_text='Было ли отправлено напоминание о заканчивающейся подписке (5 часов)'),
        ),
        migrations.AddField(
            model_name='payment',
            name='subscription_reminder_1h_sent',
            field=models.BooleanField(db_index=True, default=False, help_text='Было ли отправлено напоминание о заканчивающейся подписке (1 час)'),
        ),
    ]
