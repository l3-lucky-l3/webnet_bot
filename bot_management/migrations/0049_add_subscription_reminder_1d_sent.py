# Generated manually for subscription reminder 1 day field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0048_add_ban_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='subscription_reminder_1d_sent',
            field=models.BooleanField(db_index=True, default=False, help_text='Было ли отправлено напоминание о заканчивающейся подписке (1 день)'),
        ),
    ]
