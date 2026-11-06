# ProPresenter for Home Assistant

This Home Assistant integration connects to ProPresenter over its local
network API. It exposes controls and status as Home Assistant entities, and
includes a presentation browser that can preview slides before you put one on
screen.

## What it provides

- Media playback controls, including play, pause, and seek.
- Controls for messages, stage layouts, looks, macros, video inputs, timers,
  and individual output layers.
- Next and previous slide buttons, plus thumbnail images for current and
  neighboring slides.
- An `Active presentation` sensor with live presentation and slide details.
- On demand presentation metadata and authenticated thumbnails for the
  companion dashboard card. Slides are not added as separate Home Assistant
  entities.

The integration is fully supported with ProPresenter 19 and later. Some
features are available with ProPresenter 7.9.1 and later.

## Presentation browser

Install the companion [ProPresenter presentation card](https://github.com/media-dotcom/lovelace-propresenter-presentation)
to browse presentations from Home Assistant. The card gets its presentation
and slide data from this integration.

Choosing a playlist item loads its slides for browsing while leaving live
output alone. Clicking a slide sends that slide live. The **Enter** control
can make the selected playlist item live without choosing a specific slide,
and **Live** returns the browser to the current active presentation. Read-only
mode disables controls that send commands. Trigger requests include
presentation identity and revision checks to reject stale browser selections.

The browser uses authenticated Home Assistant WebSocket commands and
short-lived signed thumbnail URLs. No ProPresenter credentials or Home
Assistant tokens are stored in the card configuration.

## Set up ProPresenter

1. In ProPresenter, open **Settings → Network** and enable the network API.
2. For automatic discovery, enable either **Stage App** or **ProPresenter
   Remote**.
3. Keep the ProPresenter machine and Home Assistant on a network where they
   can reach each other.

## Install

### HACS

1. Open HACS in Home Assistant and search for **ProPresenter**, or use the
   [HACS repository link](https://my.home-assistant.io/redirect/hacs_repository/?owner=media-dotcom&repository=ha-propresenter-reliable-browser&category=integration).
2. Download the integration and restart Home Assistant.
3. If discovery does not start automatically, go to **Settings → Devices &
   Services → Add Integration**, search for **ProPresenter**, and enter the
   connection details.

### Manual install

Copy `custom_components/propresenter` into the `custom_components` directory
under your Home Assistant configuration, then restart Home Assistant and add
the integration from **Settings → Devices & Services**.

## Companion card

The Lovelace card is released separately. Install it through HACS as a
Dashboard repository, or add its `dist/propresenter-presentation.js` file as a
Dashboard resource. Add `custom:propresenter-presentation` to a dashboard and
select the integration's **Active presentation** sensor.

## Development

The pure backend tests can be run from this repository:

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'
```

The ProPresenter REST API documentation is available at
[openapi.propresenter.com](https://openapi.propresenter.com).
