from django.contrib import admin

from .models import GlossaryTerm, GlossaryAlias

admin.site.register(GlossaryTerm)
admin.site.register(GlossaryAlias)

# Register your models here.
