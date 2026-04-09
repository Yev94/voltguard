# Implementar Lógica Minimalista de Meross

- `[x]` Modificar `battery_backend.py`: `monitor_loop`
  - Eliminar lecturas redundantes a `plug.async_update()`.
  - Crear e inicializar una variable caché `self.last_command = None`.
  - En la condición de ON: si `% <= min_bat` y no carga -> si `last_command != "on"`, forzar un `async_update()`. Si el enchufe no está encendido realmente, mandar `turn_on()`. Independientemente de si tuvimos que mandar la orden o si ya estaba ON forzado externamente, fijar `self.last_command = "on"`.
  - En la condición de OFF: si `% >= max_bat` y carga -> si `last_command != "off"`, forzar un `async_update()`. Si el enchufe no está apagado, mandar `turn_off()`. En cualquier caso, fijar `self.last_command = "off"`.
- `[x]` Ajustar los logs de "Monitorizando..." en consola para usar la variable de caché pasiva, en vez de obligar al framework Meross a escupir su estado en cada ciclo de iteración.
