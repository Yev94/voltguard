import customtkinter as ctk
from src.ui_app import MerossBatteryApp

def run():
    app_root = ctk.CTk()
    app = MerossBatteryApp(app_root)
    app_root.mainloop()

if __name__ == "__main__":
    run()
