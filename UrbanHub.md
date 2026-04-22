# UrbanHub

## Contexte

Une Smart City génère en permanence des données provenant de multiples sources :

- Capteurs IoT : pollution, bruit, incidents
- Consommation énergétique
- Données environnementales et météorologiques
- Flux de trafic : routier, ferroviaire, mobilité douce, etc.
- Événements urbains
- …

Ces données doivent être collectées, stockées, analysées et exploitées afin de produire des indicateurs utiles aux décideurs publics.

L'objectif du projet est de développer la plateforme **UrbanHub** capable d'ingérer et d'analyser ces données. UrbanHub est ce qu'on appelle un **jumeau numérique urbain** permettant d'observer et d'analyser le fonctionnement d'une ville moderne.

---

## Architecture cible

3 types de flux de données typiques d'un système Big Data réel :

- **Flux Batch** – données historiques massives
- **Flux Streaming** – données temps réel
- **Flux IoT** – données capteurs

```
Sources de données
      │
      ▼
Ingestion (API / Scraping / Download)
      │
      ▼
Stockage Data Lake
      │
      ▼
Traitement Data Engineering
      │
      ▼
Analyse Data / IA
      │
      ▼
Indicateurs urbains
```

---

## Partie 1 – Flux Batch : analyse météorologique urbaine

### Source de données

**NOAA Global Hourly Weather Data** — observations météorologiques horaires de stations météo situées dans le monde entier.

### Travail demandé

- Écrire un script permettant de **télécharger en parallèle** les fichiers correspondant aux stations météorologiques situées en France (pas de téléchargement séquentiel fichier par fichier)
- Dimension temporelle : **depuis 2020**
- Variables à extraire :
  - `station_id`
  - `timestamp`
  - `temperature`
  - `wind_speed`
  - `wind_direction`
  - `pressure`
  - `precipitation`
  - `visibility`
- Mettre en place les nettoyages nécessaires : valeurs manquantes, conversion des unités, conversion du timestamp, etc.

### Questions métier

- Peut-on identifier des **périodes météorologiques anormales** ?
- Existe-t-il une **corrélation entre conditions météorologiques et visibilité** ?
- Quelle est l'**évolution saisonnière de la température** en France et dans les principales villes ?
- Quels sont les **jours présentant des conditions météorologiques extrêmes** ?

---

## Partie 2 – Flux Streaming : mobilité urbaine

### Source de données

**CityBikes API** — informations temps réel sur les stations de vélos en libre-service dans le monde.

### Travail demandé

- Mettre en place un système de **récupération automatique toutes les minutes** des données de stations des principales villes de France
- Couvrir la période **depuis l'existence du réseau Vélib**
- Variables à extraire :
  - `station_id`
  - `station_name`
  - `timestamp`
  - `latitude`
  - `longitude`
  - `bikes_available`
  - `free_slots`

### Questions métier

- Quelles stations présentent le **plus fort taux d'utilisation** ?
- Peut-on identifier des zones où **l'offre de vélos est insuffisante** ?
- Quels sont les **pics d'utilisation journaliers** du réseau de vélos ?
- Existe-t-il des **déséquilibres géographiques** dans la disponibilité des vélos ?
- Peut-on détecter des **stations critiques** nécessitant un rééquilibrage des vélos ?

---

## Partie 3 – Flux IoT : pollution urbaine

### Source de données

**OpenAQ** — données issues de capteurs de pollution installés dans plusieurs villes du monde.

### Travail demandé

- Collecter les données de la **France**, notamment les principales villes
- Mettre en place une **ingestion régulière simulant un flux IoT**
- Variables à extraire :
  - `sensor_id`
  - `timestamp`
  - `latitude`
  - `longitude`
  - `pollutant`
  - `value`
  - `unit`
- Polluants principaux :
  - PM2.5
  - PM10
  - NO2
  - O3
  - CO

### Questions métier

- Quelles zones présentent les **niveaux de pollution les plus élevés** ?
- Existe-t-il une **variation journalière de la pollution** ?
- Quels **polluants sont les plus dominants** dans la ville étudiée ?
- Peut-on identifier des **épisodes de pollution anormale** ?

---

## Partie 4 – Analyse croisée des données

Les différentes sources doivent être croisées afin de produire des analyses urbaines intégrées.

### Questions métier

- Existe-t-il une **relation entre conditions météorologiques et pollution** ?
- Les conditions météo **influencent-elles l'utilisation des vélos** urbains ?
- Peut-on identifier des **conditions météorologiques favorables à la mobilité douce** ?
- Existe-t-il des **périodes où mobilité, pollution et météo interagissent fortement** ?

---

## Livrables attendus

Les étudiants devront produire :

1. **Scripts de collecte des données**
2. **Stockage structuré des données**
3. **Pipeline de traitement**
4. **Analyses exploratoires**
5. **Visualisations**
6. **Interprétation des résultats**
