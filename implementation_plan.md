# Optimización del Backend y reducción de llamadas a Meross

Actualmente, el bucle de monitorización hace una petición a la API de Meross (a través de `plug.async_update()`) en paralelo cada vez que revisa la batería. El objetivo es desacoplar el estado de la batería del estado del enchufe para reducir drásticamente el uso de red. Para ello vamos a confiar en el estado del portátil (la indicación "cargando" y el porcentaje) como detonante de las acciones, consultando localmente a Meross **SÓLO** en dos casos:
1. Cuando sabemos que toca encender o apagar el enchufe, leyendo su estado antes para no mandar la orden en vano.
2. Tras pasar X minutos (configurable por el usuario) para hacer una sincronización pasiva y loguear el estado real.

## Cambios Propuestos

### Componente Lógico (Backend)
#### [MODIFY] `src/battery_backend.py`
Se rediseñará el interior de `monitor_loop`:
- En cada pasada (`check_time`), se leerá únicamente la batería mediante `psutil`.
- La lógica determinará si se entra en la "zona de carga" o "zona de descarga" basada puramente en `power_plugged`.
- Si `porcentaje <= min_bat` y la batería indica que `not cargando`:
  - En ese momento **se solicitará a Meross el estado del enchufe**.
  - Si está apagado o no responde adecuadamente, se forzará la orden de encendido.
- De la misma forma para el límite superior (`porcentaje >= max_bat` y `cargando`).
- Se implementará un cronómetro de "sincronización remota" que leerá e imprimirá el estado completo del enchufe y la batería, pero solo si ha pasado más tiempo del estipulado por el parámetro de sincronización.

### Interfaz Visua y Configurador
#### [MODIFY] `src/config_manager.py`
- Añadir a la configuración base la clave `sync_time` con valor predeterminado de `10` (minutos).
- Esto garantizará que los usuarios antiguos tengan un valor seguro si este parámetro falta.

#### [MODIFY] `src/ui_app.py`
- En el método `build_ui`, expandiré los `CHARGE PARAMETERS` agregando una caja de texto extra para introducir la "Sincronización (min)": cuántos minutos pasan antes de una lectura en frío a la red Meross.
- En la función `validate_inputs`: se capturará y se impondrán límites lógicos para `sync_time` (mínimo 1 minuto, máximo ~60 minutos).
- Recomendaremos en un hint que `check_time` (para la batería local) ahora deba ser bajo (ej. 10s-15s), al usar solo herramientas asíncronas no invasivas del SO (`psutil`).

## Preguntas Abiertas

> [!WARNING]
> Si el portátil se desconecta físicamente de la pared por error, pero el enchufe sigue encendido, el portátil informará de `power_plugged = False` y la batería empezará a caer. Una vez caiga por debajo de `min_bat`, mi código evaluaría: `"La batería está por debajo del límite y no está cargando. Voy a solicitar que se encienda el enchufe."` Comprobará a Meross, verá que el enchufe **ya está en la posición de ON** y... ahí se quedará, sin reenviar la orden (porque interpretaría que es inútil enviarle a un enchufe ENCENDIDO la señal de ENCENDIDO). 
> **Pregunta**: ¿Quieres que el código envíe una petición de "ON" de manera forzada si descubre esta redundancia, a modo de 'fail-safe'? O simplemente ignorarlo y dejar un log de advertencia ("El portátil no carga pero el enchufe parece estar ya en ON. ¡Comprueba el cable!").

## Plan de Verificación

### Pruebas Automatizadas (Manuales)
1. Abrir la interfaz y establecer un `check_interval` rápido, como 5 segundos, y el nuevo parámetro de Synchronize en `1` (minuto).
2. Comenzar el monitor y comprobar que el log produce lecturas pasivas cada 5 segundos que no interactúan con Meross, y lecturas pesadas cada exactamente 60 segundos.
3. Desconectar el portátil adrede para falsear un umbral (por ejemplo subiendo el % de mínimo en tiempo real al re-ejecutar) y monitorizar si la orden ON dispara la comprobación cruzada a Meross previamente.
