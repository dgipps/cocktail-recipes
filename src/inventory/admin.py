from django.contrib import admin

from .models import UserInventory


@admin.register(UserInventory)
class UserInventoryAdmin(admin.ModelAdmin):
    list_display = ["user", "ingredient", "in_stock", "updated_at"]
    list_filter = ["in_stock", "user", "ingredient__categories"]
    search_fields = ["ingredient__name", "user__username"]
    autocomplete_fields = ["ingredient"]
    list_editable = ["in_stock"]
    ordering = ["user", "ingredient__name"]
