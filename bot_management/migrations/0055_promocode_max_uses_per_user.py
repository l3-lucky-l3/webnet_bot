from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0054_promo_codes'),
    ]

    operations = [
        migrations.AddField(
            model_name='promocode',
            name='max_uses_per_user',
            field=models.IntegerField(default=1, verbose_name='Макс. использований на пользователя (0 = безлимит)'),
        ),
    ]
