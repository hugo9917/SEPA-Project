# Deploy gratuito

Correr esto sin costo y sin servidor propio: **GitHub Actions** hace de
scheduler, **Cloudflare R2** guarda el data lake y **Streamlit Community Cloud**
sirve el dashboard.

```
GitHub Actions (cron diario 06:00 UTC)
   └─ python -m src.daily --prune
        ├─ Cloudflare R2 ......... bronze / silver / gold
        └─ Streamlit Cloud ....... lee gold
```

El pipeline no cambia: `src/daily.py` no importa Airflow y habla el API de S3
contra el endpoint que le pases. Airflow sigue siendo el camino cuando corrés el
`docker compose` local — son dos schedulers para el mismo trabajo.

---

## Por qué no Render en el free tier

Vale la pena dejarlo escrito, porque es la primera opción que uno prueba:

| Necesita | Render free |
| :--- | :--- |
| Scheduler 24/7 | Los web services **se duermen a los 15 min**; los background workers son pagos |
| Disco persistente (MinIO) | Los discos son **add-on pago** |
| RAM para Airflow | 512 MB; scheduler + webserver piden ~1,5 GB |
| Base de metadatos | El Postgres free es de 1 GB y **expira a los 30 días** |

Render funciona con un cron job pago (~USD 7/mes) usando R2 como storage. Lo que
no funciona es la combinación *gratis + Airflow*.

---

## 1. Cloudflare R2 (storage)

Free tier: **10 GB**, 1 M escrituras y 10 M lecturas por mes. No pide tarjeta.

1. Cloudflare → **R2** → *Create bucket* → nombre `sepa-datalake`.
2. **Manage R2 API Tokens** → *Create API token* → permiso **Object Read & Write**.
3. Anotá `Access Key ID`, `Secret Access Key` y el endpoint:
   `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

> R2 sólo acepta `auto` como región. El workflow ya manda `S3_REGION=auto`; si
> corrés a mano, exportala.

### Cuánto entra en 10 GB

Bronze son ~300 MB por día y por tipo de dataset, así que sin poda un free tier
se llena en menos de dos semanas. El job corre con `--prune`, que deja sólo los
últimos `SEPA_BRONZE_KEEP_DAYS` días (7 por defecto, la misma retención que el
portal). Silver nunca se poda: es el histórico que lee el dashboard y pesa un
orden de magnitud menos.

Con los valores por defecto: ~4,2 GB de Bronze (7 días × 2 tipos) más Silver
creciendo despacio. Si te acercás al límite, bajá `SEPA_BRONZE_KEEP_DAYS` a 2 —
Bronze sólo hace falta para reconstruir Silver sin volver a bajar del portal.

---

## 2. GitHub Actions (scheduler)

En el repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Valor |
| :--- | :--- |
| `S3_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `S3_ACCESS_KEY` | Access Key ID de R2 |
| `S3_SECRET_KEY` | Secret Access Key de R2 |
| `S3_BUCKET` | `sepa-datalake` |

El workflow ya está en [`.github/workflows/daily.yml`](.github/workflows/daily.yml).
Probalo a mano desde **Actions → Ingesta diaria SEPA → Run workflow** antes de
esperar al cron.

### Tres cosas que muerden

- **GitHub apaga los cron de los repos inactivos a los 60 días.** Si el repo
  queda quieto, el workflow deja de correr sin avisar. GitHub manda un mail
  antes; alcanza con volver a habilitarlo desde la pestaña Actions, o con
  cualquier commit.
- **Minutos:** ilimitados en repos públicos, 2000/mes en privados. Una corrida
  completa de los dos datasets puede pasar la hora, así que en privado conviene
  `SEPA_MAX_COMERCIOS` o un solo tipo.
- **Techo de 6 h por job.** La primera corrida ingiere la ventana entera (7 días
  × 2 tipos) y es la más pesada. Si se pasa, corré el workflow a mano con
  `max_days: 2` un par de veces hasta emparejar, y después dejalo en 7.

---

## 3. Streamlit Community Cloud (dashboard)

1. [share.streamlit.io](https://share.streamlit.io) → *New app* → tu repo.
2. **Main file path:** `src/dashboard.py`
3. **Advanced settings → Secrets**, en formato TOML:

   ```toml
   S3_ENDPOINT_URL = "https://<ACCOUNT_ID>.r2.cloudflarestorage.com"
   S3_ACCESS_KEY = "..."
   S3_SECRET_KEY = "..."
   S3_BUCKET = "sepa-datalake"
   S3_REGION = "auto"
   ```

Streamlit expone los secrets como variables de entorno, que es justo de donde
los lee `src/config.py` — no hace falta tocar código.

> Streamlit Cloud detecta `requirements.txt` por nombre y no deja elegir otro,
> por eso ese archivo trae el set completo (ETL + dashboard). El de Airflow y el
> del cron usan `requirements-etl.txt`, sin Streamlit ni Plotly.

Para el dashboard alcanza con permisos de **lectura**: si querés, generá un
segundo token de R2 sólo-lectura para la app.

---

## Probar antes de subir

Contra R2 desde tu máquina, sin tocar nada del código:

```bash
export S3_ENDPOINT_URL="https://<ACCOUNT_ID>.r2.cloudflarestorage.com"
export S3_ACCESS_KEY="..." S3_SECRET_KEY="..." S3_BUCKET="sepa-datalake"
export S3_REGION=auto
python -m src.daily --max-days 1 --type minorista
```

Si eso escribe en R2, el workflow también va a poder.

---

## Costo

| Servicio | Free tier | Alcanza |
| :--- | :--- | :--- |
| Cloudflare R2 | 10 GB · 1 M escrituras/mes | Sí, con `--prune` |
| GitHub Actions | Ilimitado (público) · 2000 min (privado) | Sí |
| Streamlit Cloud | 1 app pública | Sí |

Total: **USD 0**. Lo que se resigna frente al stack local es la UI de Airflow —
el linaje, los reintentos por tarea y los logs por corrida. El cron de GitHub
reintenta el día siguiente vía `catch_up`, que para una ingesta diaria con siete
días de gracia alcanza.
