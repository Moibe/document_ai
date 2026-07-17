# Handoff: instalar y configurar pm2-logrotate en srviaproducto

> Este documento es para el agente/operador que trabaja directamente en el servidor `srviaproducto`. Ejecuta los pasos en orden.

## Contexto
El server (host `srviaproducto`, usuario `mbriseno`) corre 12 apps bajo pm2 (incluye `document-ai-api`, entre otras). Ninguna tiene rotación de logs configurada — los archivos en `~/.pm2/logs/*.log` crecen indefinidamente. Se confirmó vía `pm2 list` que no hay ningún proceso `pm2-logrotate` corriendo actualmente en el server.

## Objetivo
Instalar el módulo oficial `pm2-logrotate`. Rota, comprime y limpia automáticamente los logs de **todos** los procesos de pm2 del servidor. No toca código ni reinicia ninguna app — solo administra los archivos de log ya escritos en disco.

## Pasos

### 1. (Opcional) Dimensionar el problema antes de arreglarlo
```bash
du -sh ~/.pm2/logs/
ls -lh ~/.pm2/logs/
```

### 2. Instalar el módulo
```bash
pm2 install pm2-logrotate
```

### 3. Configurar límites
```bash
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'
```
(rota al pasar 10MB o diario a medianoche, lo que ocurra primero; conserva 7 archivos rotados comprimidos; borra lo más viejo)

### 4. Verificar que quedó corriendo
```bash
pm2 list
```
Debe aparecer una fila nueva `pm2-logrotate` con status `online`.

### 5. (Opcional) Confirmar la configuración aplicada
```bash
pm2 conf pm2-logrotate
```

## Notas
- Aplica a las 12 apps existentes automáticamente — es configuración a nivel del daemon de pm2, no por app.
- No requiere reiniciar `document-ai-api` ni ninguna otra app.
- Reversible en cualquier momento: `pm2 uninstall pm2-logrotate`.

---
*Este archivo es un handoff operativo, no documentación del proyecto document_ai — puede borrarse del repo una vez ejecutados los pasos en el server.*
