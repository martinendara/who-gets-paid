# CLAUDE.md — repo `who-gets-paid` (nombre provisorio)

Repo público del piloto de auditoría de atribución para el Mozilla Fellowship 2027. Leé SPEC.md y el HANDOVER vigente antes de cualquier cambio.

## Qué hay acá

Un runner que consulta motores de IA por API oficial, un parser de citas, un clasificador y el cálculo de métricas, más los datos y el método publicados. Nada más.

## Reglas duras

- **Nunca pushees.** Commiteás por bloque; Martín revisa el diff crudo y pushea.
- **Cero código de Lotus o Clicon** salvo que el HANDOVER registre una decisión D que lo autorice para ese módulo puntual. El repo tiene que correr solo.
- **Los datos privados no se commitean:** `data/private/` está en `.gitignore`, junto con URLs reales, nombres de medios y claves. Antes de cada commit, revisá que no se cuele nada de eso.
- **Las claves van solo por variables de entorno.** Antes de cualquier corrida, verificá que estén cargadas (`echo ${#OPENAI_API_KEY}` y similares) y no imprimas su valor.
- **El clasificador no puede ser un motor auditado.** El prompt vive en `prompts/` y cada cambio suma una versión.
- **Toda métrica devuelve `null` si su denominador es 0,** nunca 0. Un ratio que pueda pasar de 1.0 es un bug.
- **METHOD.md no se edita después de la primera corrida real.** Cualquier cambio de método va como anexo con fecha.

## Cómo trabajar

1. Si la sesión arranca con un audit, es de solo lectura: describís qué hace el código, sin editar ni proponer fixes.
2. Implementás solo lo que dicen las decisiones D cerradas y el prompt de implementación recibido.
3. Un commit por bloque, con un mensaje que declara la intención (no el comportamiento).
4. Si el prompt de diseño está mal o contradice la SPEC, frená y decilo. Los frenos se anotan como aciertos.

## Comandos (se completan cuando exista el código)

```bash
python -m src.run --file data/smoke.csv --engines gemini,claude --run-id smoke   # smoke (IMPL-01)
python -m src.run --file data/private/queries.csv --engines gemini,openai,claude,perplexity --run-id <id>
python -m src.classify                # clasificación automática (IMPL-02)
python -m src.metrics > results/metrics.json
```

## Stack

Python 3.12 y la librería estándar donde alcance; dependencias mínimas en `requirements.txt`. Sin base de datos, sin Docker y sin integración continua hasta después del grant.
