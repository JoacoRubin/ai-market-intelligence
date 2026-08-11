"""Captura de ejecuciones reales del agente para el replay estático.

Este paquete NO forma parte del runtime. Es una herramienta de build: corre el
grafo completo contra el modelo y la base reales, y congela cada ejecución en
JSON para que un sitio estático la reproduzca sin infraestructura detrás.

Existe por una restricción concreta del proyecto: el stack completo —Ollama,
torch, embeddings y SQL Server— pide unos 8 GB de RAM, y ningún hosting gratuito
lo sostiene. En vez de pagar por una caja encendida las 24 horas para un demo
que se visita de a ratos, se ejecuta el agente localmente y se publica el
resultado. Costo de hosting: cero.

Y hay un efecto secundario que resultó ser lo mejor del arreglo: el replay
**muestra más** que un demo en vivo. En vivo el visitante espera un minuto y ve
un PDF. Acá ve el grafo etapa por etapa, con los tiempos reales de cada nodo,
las citas con su identificador y el forecast contra su baseline. Todo eso existe
en `Report` y en una corrida en vivo queda invisible.

Lo que este paquete no hace, a propósito: inventar. Si un dato no estuvo en la
ejecución, no aparece en la captura.
"""
