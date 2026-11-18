# Module Woob Fortuneo vendorisé

- Module : `fortuneo`
- Version : `202605011706`
- Source : dépôt officiel Woob (`https://updates.woob.tech/3/main/`)
- Licence : LGPLv3+ (en-têtes conservés dans chaque fichier)
- Compatibilité déclarée : `woob ~= 3.2` ; testé ici avec Woob `3.7`
- Fichiers copiés : `__init__.py`, `browser.py`, `module.py`, `pages/`,
  `requirements.txt` (les tests upstream ne sont pas nécessaires au runtime)

Le module n'est pas mis à jour automatiquement. Pour tester une version plus
récente, installer le module dans un répertoire séparé et pointer
`FORTUNEO_SIDECAR_MODULES` vers ce répertoire, puis relancer les tests et le
smoke test réel.

## Correctifs locaux

Le module de base `202605011706` ne gère pas la SCA SMS qui peut être exigée
pendant le flux OAuth-PKCE. Les correctifs suivants sont appliqués localement,
en attendant leur fusion upstream :

- **SCA SMS sur `request_authorization()`** : détection des réponses
  `403` JSON `SCA_REQUESTED` (et `SCA_REQUIRED` par tolérance), envoi du code
  via `POST https://api.fortuneo.fr/security/operation/otp`, puis reprise de
  `authorization-code` avec `authenticationMethod` et `operationId`. Adaptation
  de la MR Woob `!1129` (`fortuneo-sca-sms`) et de la MR `!1110`.
  Lors de la reprise, seuls les refus `401`/`403` sont convertis en code SMS
  invalide ; les autres erreurs réseau ou serveur remontent telles quelles.
- **Code OAuth** : lecture du `code` dans la query **ou** le fragment de
  l'URL de callback, comme le front-end Fortuneo. Les erreurs OAuth
  (`access_denied`, `FINGERPRINT_NOT_FOUND`, `session_expired`, etc.) sont
  converties en exceptions Woob explicites, sans journaliser l'URL ni ses
  valeurs.
- **Champ `sca_code`** ajouté à la configuration du module, distinct du champ
  `code` utilisé par la 2FA historique.

Ne sont pas repris dans ce correctif :

- la reprise de session portail pendant 10 minutes (commit `54734f71` de la
  MR `!1129`) ;
- la mise à jour du module avec les commits `master` postérieurs
  (transactions PEA/compte courant, assurance-vie).

Références : Woob issue `#864`, MR `!1129`, MR `!1110`.
