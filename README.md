# ProPresenter Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz/)

This custom integration allows you to control [ProPresenter](https://www.renewedvision.com/propresenter) presentation software from Home Assistant. Fully functional with ProPresenter v19 and up. Partial functionality with v7.9.1 and up.

## Some of the Features

- **Media & Audio Player Integration**: Full media player entity for controlling playback with seek and playlist support
<img width="524" height="192" alt="image" src="https://github.com/user-attachments/assets/8913e5ed-6249-4fc9-a194-7012a9a8acab" />

- **Message Control**: Show and hide message buttons for each configured message
- **Stage Screen Layouts**: Select dropdowns to switch between stage layouts
- **Looks Management**: Select entity for switching between ProPresenter looks
<img width="529" height="385" alt="image" src="https://github.com/user-attachments/assets/99c41ec8-ee86-4e77-80e2-c0e7e6a67883" />

- **Clear Layer Controls**: Switch entities to clear different ProPresenter layers (audio, messages, props, announcements, slide, media, video input)
- **Macro Selection**: Select entity to trigger ProPresenter macros
- **Video Input Selection**: Select entity for switching video inputs
- **Timer Management**: Control and monitor ProPresenter timers with reset buttons
<img width="514" height="450" alt="image" src="https://github.com/user-attachments/assets/67d08e63-11ed-4915-9163-e7d6282a7eed" />


- **Slide Control**: Button entities for Next Slide and Previous Slide actions
- **Presentation Thumbnails**: Image entities displaying slide thumbnails for easy reference
<img width="751" height="324" alt="image" src="https://github.com/user-attachments/assets/44f10684-43d3-45a9-a91a-550a7a76f9d6" />

## Dynamic presentation browser

The integration also exposes one stable `Active presentation` sensor per
ProPresenter config entry. It keeps scalar live state in Home Assistant and
serves the changing slide collection on demand over an authenticated WebSocket
command. Slide thumbnails are authenticated, revision-aware, and bounded by a
shared per-entry cache; individual slides are not created as Home Assistant
entities.

The companion dashboard card is maintained separately at
`https://github.com/media-dotcom/lovelace-propresenter-presentation`. Install its
`dist/propresenter-presentation.js` resource through HACS, then add
`custom:propresenter-presentation` and select the Active presentation sensor in
the visual editor. The card supports read-only monitoring, guarded triggering,
explicit refresh, disabled-slide preservation, and a follow-live toggle.

The trigger service is `propresenter.trigger_slide`. It accepts the original
0-based `slide_index` and optional `expected_presentation_uuid` plus
`expected_metadata_revision` guards. The card supplies both guards and uses the
active-presentation trigger endpoint, so it does not focus a different
presentation in ProPresenter.

## Video Guide

<a href="https://youtu.be/4U3ebJGnlks">
    <img src="https://img.youtube.com/vi/4U3ebJGnlks/maxresdefault.jpg" alt="Watch the video" style="width: 50%; border-radius: 8px;">
</a>

## Installation

### ProPresenter Setup

Make sure the ProPresenter API is enabled:

1. Open ProPresenter
2. Go to **ProPresenter** → **Settings** → **Network** → **Enable Network**
<img width="491" height="220" alt="image" src="https://github.com/user-attachments/assets/50f08cbf-0a53-4d06-91e8-d7a9b5557e75" />

3. Optionally enable either **Stage App** or **ProPresenter Remote** to enable auto-discovery in Home Assistant

### Home Assistant Setup - HACS (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance
2. Search for  `ProPresenter` in HACS or click the button below:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=media-dotcom&repository=ha-propresenter-reliable-browser&category=integration)

3. Click **Download** on the ProPresenter integration
4. Restart Home Assistant
5. ProPresenter should be automatically discovered under **Settings** → **Devices & Services**
<img width="325" height="253" alt="image" src="https://github.com/user-attachments/assets/2a07803f-d98d-43be-9b04-1736978db00f" />

6. Otherwise go to **Settings** → **Devices & Services** → **Add Integration**
7. Search for `ProPresenter` and follow the setup instructions

### Manual Installation

1. Copy the `propresenter` folder to your Home Assistant `custom_components` directory:
   ```
   <config_directory>/custom_components/propresenter/
   ```

2. Restart Home Assistant

3. Refer to step 5 above


## Other Noteworthy Projects
Home Assistant ProWebRemote Add-on
https://github.com/BenJamesAndo/ha-addons/tree/main/prowebremote

<img width="1917" height="925" alt="image" src="https://github.com/user-attachments/assets/0018fa65-788c-4dc6-b94d-434e8cf163e5" />

## API Reference

This integration uses the ProPresenter REST API v1.
Full API documentation: https://openapi.propresenter.com

## Credits

- greyshirtguy for his wonderful and tireless work on https://github.com/bitfocus/companion-module-renewedvision-propresenter-api
- ProPresenter by Renewed Vision: https://renewedvision.com/propresenter
