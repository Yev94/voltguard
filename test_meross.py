import asyncio
import os
import getpass
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager

async def test_meross():
    print("=== Tester de Enchufe Meross ===")
    
    # 1. Solicitar credenciales
    email = input("Introduce tu email de Meross: ")
    password = getpass.getpass("Introduce tu contraseña (no se mostrará): ")
    
    print("\n[+] Conectando a la nube de Meross...")
    try:
        # 2. Iniciar sesión en la API (cloud)
        http_api_client = await MerossHttpClient.async_from_user_password(
            api_base_url="https://iotx-eu.meross.com",
            email=email,
            password=password
        )
        
        # 3. Inicializar el Manager
        manager = MerossManager(http_client=http_api_client)
        await manager.async_init()
        
        # 4. Descubrir dispositivos
        print("[+] Buscando dispositivos y esperando a que se reporten (esto puede tardar unos segundos)...")
        await manager.async_device_discovery()
        
        plugs = manager.find_devices(device_type="mss315")
        all_devices = manager.find_devices()
        
        if len(all_devices) > 0:
            print("\n✔️ Dispositivos totales encontrados en tu cuenta:")
            for dev in all_devices:
                print(f"  - {dev.name} (Tipo: {dev.type}, En línea: {dev.online_status})")
        else:
            print("\n❌ No se ha encontrado ningún dispositivo en tu cuenta.")
            
        if len(plugs) > 1:
            print("\n✅ ¡ÉXITO! Se han encontrado varios enchufes MSS315.")
            plug = plugs[1] # Selecciona el enchufe 2
            print(f"👉 Usando específicamente el segundo enchufe: {plug.name}")
            await plug.async_update()
            
            # 5. Probar control
            print("\n[+] Prueba 1: Encendiendo enchufe...")
            await plug.async_turn_on()
            print("  -> ¡Comando de encendido enviado!")
            
            await asyncio.sleep(3)
            
            print("[+] Prueba 2: Apagando enchufe...")
            await plug.async_turn_off()
            print("  -> ¡Comando de apagado enviado!")
            
            print("\n🚀 CONCLUSIÓN: ¡El dispositivo FUNCIONA con la librería!")
        else:
            print("\n⚠️ RESULTADO: No se encontró un modelo 'mss315'. (Quizás es un modelo Matter que no aparece en la API antigua).")
            print("Te recomendaría devolverlo y comprar algo como TP-Link Kasa o Shelly.")
            
    except Exception as e:
        print(f"\n❌ Se produjo un error: {e}")
        
    finally:
        try:
            manager.close()
            await http_api_client.async_logout()
        except:
            pass

if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_meross())
