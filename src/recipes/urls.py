"""Frontend URL routes for recipes."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.recipe_list, name="recipe_list"),
    path("available/", views.available_recipes, name="available_recipes"),
    path("recommend/", views.chat_page, name="chat_page"),
    path("chat/", views.chat_message, name="chat_message"),
    path("chat/clear/", views.chat_clear, name="chat_clear"),
    path("<slug:slug>/", views.recipe_detail, name="recipe_detail"),
]
