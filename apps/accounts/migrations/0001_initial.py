from django.db import migrations, models
import django.contrib.postgres.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField(unique=True)),
                ('level', models.CharField(blank=True, max_length=20)),
                ('markets', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=20), blank=True, default=list, size=None)),
                ('style', models.CharField(blank=True, max_length=20)),
                ('goal', models.CharField(blank=True, max_length=20)),
                ('capital', models.CharField(blank=True, max_length=10)),
                ('onboarding_done', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'user_profile',
                'managed': False,
            },
        ),
    ]
