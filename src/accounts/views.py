from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render


def signup(request):
    if request.user.is_authenticated:
        return redirect("recipe_list")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_staff = False
            user.is_superuser = False
            user.is_active = False
            user.save()
            return render(request, "accounts/signup.html", {"form": None, "pending": True})
    else:
        form = UserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})
