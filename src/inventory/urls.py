from django.urls import path
from . import views

urlpatterns = [
    path("", views.inventory_page, name="inventory"),
    path("toggle/", views.toggle_ingredient, name="inventory_toggle"),
]
