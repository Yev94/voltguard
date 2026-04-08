import customtkinter as ctk
from src.ui_app import VoltGuardApp

def run():
    app_root = ctk.CTk()
    app = VoltGuardApp(app_root)
    app_root.mainloop()

if __name__ == "__main__":
    run()
