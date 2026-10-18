# Connecting social accounts

This guide covers the developer apps each platform needs before you can connect accounts in
**Settings → Social accounts**. Each section is also shown in the app under the platform's
**Setup guide**.

How it fits together:

1. Create a developer app on the platform (once).
2. Put its credentials in **Settings → Integrations → Social apps and Google**.
3. Register the app's **redirect URI** exactly as the app shows it (Social accounts page, next to each platform).
4. Press **Connect** on the Social accounts page and approve access.

The redirect URI has this shape:

```
{PUBLIC_BASE_URL or http://localhost:8600}/api/v1/social/oauth/{provider}/callback
```

where `{provider}` is `meta`, `threads`, `linkedin`, `pinterest`, `google`, `x`, `tiktok` or `canva`. If you set
`PUBLIC_BASE_URL` in `.env` (for example a Cloudflare Tunnel address), the redirect URI changes,
so register the new one in each developer app too.

Platform consoles change their wording often. Where a step names a button, look for the closest
match; the goal of each step is stated so you can find it.

<!-- guide:meta -->
## Meta: Facebook pages and Instagram

Goal: a Meta app that signs you in with Facebook and can post to the Pages you manage and to the Instagram
professional accounts linked to them.

Before you start:

- Your Instagram account must be a **Business** or **Creator** account and linked to your Facebook Page
  (Instagram app → Settings → Account type and tools, then link the Page in Meta Business Suite or Page settings).
- You need full control (or content) access on the Page.

### Step 1: register as a Meta developer (once)

If developers.facebook.com shows no **Create app** button, or **My Apps** is missing, your Facebook account isn't
registered as a developer yet.

1. Sign in to Facebook with the account that manages the Page.
2. Open [developers.facebook.com/async/registration](https://developers.facebook.com/async/registration)
   (or press **Get started** at the top right of developers.facebook.com).
3. Accept the Platform Terms, verify a **phone number** and an **email address** with the codes Meta sends,
   and choose an occupation (for example Developer).
4. After that, **My Apps** appears at the top right.

If verification loops or the phone code never arrives, try another browser, turn off ad blockers, and make sure the
Facebook account itself has a confirmed phone number (Facebook → Settings → Accounts Centre → Personal details).

### Step 2: create the app

1. Open [developers.facebook.com/apps/creation](https://developers.facebook.com/apps/creation/) (or My Apps → **Create app**).
2. **App details:** name it, for example `HRP AI Studio`, and enter your contact email. Next.
3. **Use cases:** tick **Manage everything on your Page**. Use cases that can't be combined with it are greyed out.
   Next.
4. **Business:** pick your business portfolio, or **I don't want to connect a business portfolio yet**. Next.
5. **Requirements** and **Overview:** read and press **Go to dashboard**.

### Step 3: permissions and redirect URI

1. In the dashboard open **Use cases → Manage everything on your Page → Customize**. Under **Permissions**, press
   **Add** next to `pages_manage_posts`, `pages_read_engagement`, `pages_read_user_content` and `read_insights`
   (`pages_show_list`, `business_management` and `public_profile` are already there).
2. For Instagram, go back to **Use cases → Add use cases** and add **Manage messaging and content on Instagram**,
   then choose **API setup with Facebook Login** (not Instagram Login). Add `instagram_basic`,
   `instagram_content_publish` and `instagram_manage_insights`.
   If Meta won't combine it with the Page use case in one app, create a second app for Instagram and tell me;
   for now you can connect Facebook Pages alone.
3. Open **Facebook Login for Business → Settings** (left menu, or inside the use case's **Settings**). In
   **Valid OAuth Redirect URIs** paste the redirect URI shown on the Facebook and Instagram card in this app
   (it ends in `/oauth/meta/callback`), and press **Save changes**. Leave **Login with the JavaScript SDK** off.
   If `http://localhost` is refused (it always is once the app is Live), set up the HTTPS address (see "HTTPS address").
4. Open **App settings → Basic**, copy the **App ID** and press **Show** to copy the **App secret**.

### Step 4: connect

1. In this app, on **Social accounts**, press **Set up app** on the Facebook and Instagram card and paste the
   App ID and App secret.
2. Press **Connect Facebook and Instagram**, sign in, and tick every Page (and Instagram account) you want to manage.
   Each Page and linked Instagram account becomes a separate account here; remove the ones you don't need.

Notes:

- Leave the app **unpublished** (development mode). That is enough for your own Pages, because people with a role on
  the app (you, as admin) can use every permission without App Review. Publishing and App Review are only needed to
  let other businesses connect.
- Page tokens obtained this way don't expire, but they stop working if you change your Facebook password, remove
  the app, or lose your role on the Page. The app checks every 6 hours and asks you to reconnect when needed.
- Instagram fetches media from a public URL, so set up **public media hosting** first.

<!-- guide:threads -->
## Threads

Goal: the Threads use case on your Meta app, so this app can post to your Threads profile.
Meta allows the Threads use case in the same app as **Manage everything on your Page**.

1. In your Meta app open **Use cases → Add use cases** and add **Access the Threads API**
   (or create a new app with only that use case).
2. Open it with **Customize** and press **Add** next to `threads_content_publish` and `threads_manage_insights`
   (`threads_basic` is always included).
3. In the use case's **Settings**, paste the redirect URI shown on the Threads card in this app (ends in
   `/oauth/threads/callback`) into **Redirect callback URLs**, and put the same address in **Uninstall callback URL**
   and **Delete callback URL**. Save. If an `http://localhost` address is refused, set up the HTTPS address (see "HTTPS address").
4. Still in Settings, press **Add or remove Threads testers**, add your Threads username with the **Threads Tester**
   role, then accept the invite on threads.net → Settings → Account → Website permissions → Invites
   (or in the Threads app).
5. The same Settings page shows a **Threads app ID** and **Threads app secret**, which differ from the main App ID.
   Paste them in **Integrations → Meta app** into the Threads app ID / Threads app secret fields.
6. Press **Connect Threads** on the Social accounts page.

Threads tokens last 60 days; the app refreshes them automatically.
Media is fetched from a public URL, so set up **public media hosting**.

<!-- guide:linkedin -->
## LinkedIn: your profile and company pages

Goal: a LinkedIn app that can post on your own profile and, once approved, on company pages you administer.

1. Go to [linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) and create an app. It must be
   associated with a LinkedIn company page (use your company page; you need to be its admin) and verified by that
   page (the app's **Settings** tab offers a verification link for a page admin).
2. In the **Products** tab, add:
   - **Sign In with LinkedIn using OpenID Connect** (scopes `openid`, `profile`, `email`), and
   - **Share on LinkedIn** (scope `w_member_social`).
   Both are self-serve and approved immediately.
3. Company pages need the **Community Management API**, and LinkedIn only allows it on an app with **no other
   products** (that's why its "Request access" button is greyed out on an app that already has the two products
   above). So create a **second app** for company pages:
   - Create another app linked to the same company page and verify it from the page (Settings tab).
   - In its **Products** tab, request **Community Management API** only. Fill in the form with your registered
     legal organization, a business email on your domain (not Gmail), website and privacy policy URL.
   - LinkedIn reviews the request (days to weeks). You first get the Development tier; Standard tier needs a
     screen recording of the app in use.
   - Add the same redirect URI to this second app's **Auth** tab.
4. In the **Auth** tab, add the redirect URI shown in this app (provider `linkedin`) under
   **Authorized redirect URLs for your app**. Copy the **Client ID** and **Client secret**.
5. In this app: **Social accounts → LinkedIn → Set up app**, paste the first app's Client ID and secret and, once
   you have it, the company-pages app's Client ID and secret in the optional section. Save.
6. Press **Connect** for LinkedIn to connect your profile. After the Community Management API is approved, turn on
   **Include company pages** and press **Connect** again: this sign-in uses the company-pages app.

Notes:

- LinkedIn access tokens last about 60 days. Refresh tokens are only issued to some apps (for example those with
  the Community Management API). When there is none, the app warns you 7 days before expiry so you can reconnect.
- Company pages: you must be a **Super admin** or **Content admin** of the page.

<!-- guide:pinterest -->
## Pinterest

Goal: a Pinterest app that can create pins on your boards.

1. Convert your Pinterest account to a **business account** (free) if it isn't one.
2. Go to [developers.pinterest.com/apps](https://developers.pinterest.com/apps/) and request access / create an
   app. New apps get **Trial access**.
3. In the app's settings, add the redirect URI shown in this app (provider `pinterest`). Copy the **App ID** and
   **App secret key**.
4. In this app: **Integrations → Pinterest app**, paste them and save.
5. On **Social accounts**, press **Connect** for Pinterest, then open the account's **Settings** and pick the
   default board for new pins.

Trial vs Standard access:

- With **Trial access**, pins you create may be visible only to you, and some apps must use the sandbox API
  (turn on **Use the sandbox API** in the account settings if Pinterest says so).
- Apply for **Standard access** from the app page. Pinterest asks for a short **screen recording** showing the
  OAuth flow and a pin being created through your integration: record connecting Pinterest here and publishing a
  test pin. Approval usually takes one to two weeks.

Tokens last 30 days and are refreshed automatically with the refresh token.

What gets posted: one image (JPG or PNG; WebP and GIF images are sent as a JPG copy), a carousel of 2–5 images,
or a video (animations use their MP4 version; an attached image becomes the video cover). The post's title, text
and link become the pin's title, description and destination link. Published pins can be deleted from the post's
page in this app. Pinterest's API is free; nothing here needs a paid plan.

<!-- guide:google -->
## Google: Business Profile, YouTube, Analytics and Search Console

Goal: one Google Cloud OAuth client that lets this app post to your Business Profile, upload YouTube Shorts,
and read Google Analytics 4 and Search Console.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project.
2. Enable these APIs (APIs & Services → Library):
   - **Google Analytics Data API** and **Google Analytics Admin API**
   - **Google Search Console API**
   - **YouTube Data API v3**
   - The Business Profile APIs: **My Business Account Management API**, **My Business Business Information API**
     and the **Google My Business API** (local posts). These only appear after Google approves your
     **Business Profile API access request** (search for "Business Profile APIs prerequisites" and submit the
     request form with your project number). Approval can take a few weeks; the rest works meanwhile.
3. Set up the **OAuth consent screen** (Google Auth Platform): user type **External**, publishing status
   **Testing**, and add your own Google account as a **test user**. Add the scopes for Business Profile
   (`business.manage`), Analytics (`analytics.readonly`), Search Console (`webmasters.readonly`) and YouTube
   (`youtube.upload`, `youtube.readonly`).
4. Create credentials: **OAuth client ID**, application type **Web application**. Add the redirect URI shown in
   this app (provider `google`) under **Authorized redirect URIs**. Copy the **Client ID** and **Client secret**.
5. In this app: **Integrations → Google Cloud OAuth client**, paste them and save. (Optional: a PageSpeed Insights
   API key for site speed reports.)
6. On **Social accounts**, press **Connect** for Google. Your YouTube channel is added automatically. For Business
   Profile, open **Choose locations** and pick the locations to post to.
7. In **Analytics** settings, choose the GA4 property and the Search Console site.

Notes:

- While the consent screen is in **Testing**, Google refresh tokens expire after **7 days**, so you would have to
  reconnect weekly. To avoid that, set the publishing status to **In production**. For a personal app using only
  your own account you can do so without verification (Google shows an "unverified app" warning you can accept);
  sensitive/restricted scopes for other users need verification.
- YouTube uploads use the free daily quota (an upload costs about 1,600 of the default 10,000 units).
  Videos uploaded by unverified API projects may be locked to private until the project passes a YouTube API
  audit; the account settings let you choose public, unlisted or private.

<!-- guide:bluesky -->
## Bluesky

Goal: an app password that lets this app post for you without your main password.

1. In Bluesky, open **Settings → Privacy and security → App passwords** and add a new app password.
   Leave direct messages access off.
2. On **Social accounts**, choose **Add Bluesky**, enter your handle (for example `hrpai.bsky.social`) and the
   app password.
3. If your account lives on your own PDS rather than bsky.social, enter its address under **Server**.

The app password is stored encrypted and used to renew the session when needed. Delete it in Bluesky to revoke
access.

<!-- guide:mastodon -->
## Mastodon

Goal: an access token for your account on your Mastodon server.

1. On your Mastodon server, open **Preferences → Development → New application**.
2. Name it (for example "HRP Digital Marketing"), leave the redirect URI as is, and select the scopes
   `read:accounts`, `read:statuses`, `write:statuses` and `write:media`.
3. Save, open the application and copy **Your access token**.
4. On **Social accounts**, choose **Add Mastodon**, enter the server address (for example `https://mastodon.social`)
   and the token.

<!-- guide:x -->
## X (Twitter)

Connecting X is **optional**. Without it you can still add X as a manual account (below) and post by hand.

Goal: an X developer app with OAuth 2.0 that can post for your account.

About cost: X's API is sold in plans. The free tier (where X still offers it) allows only a small number of posts
a day and no reading of post numbers; pay-per-use plans charge credits per request and stop when credits run out.
This app never needs a paid plan: when X refuses something because of your plan, the post or account shows a plain
explanation instead of an error, and nothing else in the app depends on X.

1. Sign in at [developer.x.com](https://developer.x.com/en/portal/dashboard) with the X account you post from and
   create a **Project** and an **App** inside it (free sign-up).
2. Open the app → **User authentication settings → Set up**:
   - App permissions: **Read and write**.
   - Type of App: **Web App, Automated App or Bot** (a confidential client).
   - Callback URI / Redirect URL: the redirect URI shown on the X card in this app (provider `x`). X refuses
     `localhost` addresses, so set up the HTTPS address (Cloudflare Tunnel, below) first.
   - Website URL: your company website.
3. Save, then open **Keys and tokens → OAuth 2.0 Client ID and Client Secret** and copy both.
4. In this app press **Set up app** on the X card (or **Integrations → X (Twitter) app**), paste them and save.
5. Press **Connect X (Twitter)** and approve. The app asks for `tweet.read tweet.write users.read offline.access
   media.write`; access renews itself automatically.

What gets posted: text, up to 4 images, or one GIF or video. Text longer than 280 characters (links count as 23)
is split into a numbered thread at paragraph and sentence breaks, with the media on the first post. If your
account has X Premium, switch on **Post long text as one post** in the account's settings.

Post numbers (views, likes, replies, reposts) are **off by default** because reading them costs credits or isn't
allowed on the free tier. Turn on **Collect post numbers** in the account's settings if your plan includes it; if
X refuses, collection switches itself off and the account shows why.

Published X posts (and every part of a thread) can be deleted from the post's page in this app.

<!-- guide:tiktok -->
## TikTok

Connecting TikTok is optional and free. Goal: a TikTok developer app with Login Kit and the Content Posting API
(Direct Post) that can publish videos and photo posts to your account.

1. Sign in at [developers.tiktok.com](https://developers.tiktok.com/apps/) and **Create an app** (free).
2. Fill in the app details (icon, name, category, description, terms and privacy policy links on your website).
3. Add products: **Login Kit** and **Content Posting API**. In Content Posting API switch on **Direct Post**.
4. Scopes: `user.info.basic`, `video.publish`, `video.upload`.
5. Login Kit → **Redirect URI**: the one shown on the TikTok card in this app (provider `tiktok`). TikTok only
   accepts HTTPS, so set up the HTTPS address (Cloudflare Tunnel, below) first.
6. Photo posts (and videos sent by link) are fetched by TikTok from your public media address. Under **URL
   properties**, add and verify the domain of that address (for example `studio.hrpai.in`, or your Cloudinary
   domain). Videos are uploaded directly by default, which needs no verification.
7. Copy the **Client key** and **Client secret**. In this app press **Set up app** on the TikTok card, paste them
   and save, then press **Connect TikTok**.

**Unaudited apps post privately.** Until TikTok audits your app, every post is published with privacy
**Only me**: you see it on your profile, nobody else does. This is TikTok's rule, not an error. To post publicly,
submit the app for review in the developer portal (TikTok asks for a demo video of connecting and posting).
Once approved, open the account's **Settings** here, switch on **My TikTok app passed the audit** and choose who can
see new posts (Everyone, Friends, Followers or Only me). The app checks TikTok's allowed choices and the
maximum video length for your account before each post.

What gets posted: one vertical video (animations use their MP4 version) with the caption (up to 2,200
characters including hashtags), or a photo post of up to 35 images (sent as JPG). TikTok processes the post
for a little while; the app waits and links to it when it's live. Post numbers aren't collected for TikTok.
TikTok's API doesn't allow deleting posts, so delete them in the TikTok app.

<!-- guide:canva -->
## Canva (design import)

Connecting Canva is optional and free (any Canva plan, including Canva Free). Goal: a Canva Connect API
integration so you can pick designs in **Media studio → From Canva** (and in a post's **Create media → From Canva**)
and import them into the media library.

1. Sign in at [canva.com/developers](https://www.canva.com/developers/integrations/connect-api) and choose
   **Create an integration** (public or private; private is fine for your own team).
2. Configuration → **Scopes**: `design:meta:read`, `design:content:read`, `asset:read`, `profile:read`.
3. Authentication → **Authorized redirects**: add the redirect URI shown in this app (provider `canva`). Canva
   doesn't accept `localhost`, so use the HTTPS address (Cloudflare Tunnel, below).
4. Copy the **Client ID** and generate a **Client secret**.
5. In this app: **Integrations → Canva integration**, paste them and save.
6. Open **Media studio → From Canva → Connect Canva** and approve.

Then search your designs, pick the pages you want and a format (PNG, JPG, PDF, MP4 or GIF). Canva exports them
and the files land in the media library, ready to attach to posts.

<!-- guide:manual -->
## Other platforms (publish by hand), and X without the API

Add any platform (or X, if you don't want to use the X API) as a **manual account**. Posts for manual accounts are
prepared as usual and wait in **Manual publish** with "copy text", "download media" and "open platform" actions.
Publish them by hand and mark them published.

<!-- guide:linkinbio -->
## Link-in-bio page

**Link in bio** (in the Content menu) builds a free, mobile-friendly page with your logo, name, tagline, social
icons, buttons and (optionally) your latest posts, in your brand colours. Put its address in your Instagram,
TikTok and X bios.

- Address: `PUBLIC_BASE_URL/l/<your-page-name>` (for example `https://studio.hrpai.in/l/hrp-ai`), and on this laptop
  `http://localhost:8600/l/<your-page-name>`.
- Only published pages are shown. Unpublish it any time.
- Clicks are counted per button per day. No names, IP addresses or cookies are stored.
- Through the tunnel only the page itself, its button redirects (`/l/<name>/go/...`) and its images
  (`/l/<name>/img/...`) are reachable, and only for reading.

<!-- guide:media -->
## Public media hosting (Instagram, Threads, Business Profile)

Instagram, Threads and Google Business Profile download images and videos from a public URL. Your laptop isn't
reachable from the internet, so choose one:

**Option A: Cloudinary (free plan, simplest)**

1. Create a free account at [cloudinary.com](https://cloudinary.com).
2. In the Cloudinary console, find the **Cloud name**, **API key** and **API secret**.
3. In this app: **Integrations → Search and hosting → Cloudinary**, paste them and save.
   Media is uploaded just before publishing.

**Option B: your own HTTPS address (Cloudflare Tunnel, free, recommended)**

This one address also fixes sign-in: once a Meta app is **Live**, Facebook, Instagram and Threads refuse
`http://localhost` redirect URIs, and Pinterest needs HTTPS too. See "HTTPS address" below.

Only signed media links are served publicly; the rest of the API still requires sign-in.

## HTTPS address (Cloudflare Tunnel)

Goal: a stable address such as `https://studio.hrpai.in` that forwards to this app on your laptop, so Meta and
Pinterest can return to the app after sign-in and Instagram/Threads can fetch your media. It costs nothing and
needs no open ports on your router.

**What is exposed:** only the sign-in return addresses (`/api/v1/social/oauth/.../callback`), signed media links
(`/api/v1/media/public/...`) and published link-in-bio pages (`/l/...`). The app itself refuses every other request that comes through the tunnel, even with a
valid login, so the web app, API and admin screens stay reachable only from this laptop.

1. Sign in to [dash.cloudflare.com](https://dash.cloudflare.com) (the account that manages `hrpai.in`) and open
   **Zero Trust** (left menu). If asked, choose a team name and the **Free** plan.
2. Go to **Networks → Tunnels → Create a tunnel**, choose **Cloudflared**, name it `hrp-digital-marketing`, and save.
3. On "Install and run a connector", choose **Windows**. The command shown ends with a long token after
   `--token`. Copy **only that token**. You don't need to run the command; `start.ps1` does it.
4. Press **Next** and add a **public hostname**:
   - Subdomain: `studio`  · Domain: `hrpai.in`
   - Path: `^/(api/v1/(social/oauth/|media/public/|health$)|l/)` (the `l/` part serves the link-in-bio page)
   - Service: Type **HTTP**, URL `localhost:8600`

   Save the tunnel.
5. Add these two lines to `.env` in the Digital Marketing folder (paste your token):

   ```
   PUBLIC_BASE_URL=https://studio.hrpai.in
   CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...your token...
   ```
6. Run `.\stop.ps1` then `.\start.ps1`. The first start downloads `cloudflared` into `tools\`. The summary shows
   `HTTPS: https://studio.hrpai.in`, and Cloudflare shows the tunnel as **Healthy**.
7. The Social accounts page now shows HTTPS redirect URIs for Facebook and Instagram, Threads, Pinterest, X, TikTok
   and Canva, for
   example `https://studio.hrpai.in/api/v1/social/oauth/meta/callback`. Put that exact URI in each developer app
   (Meta: **Facebook Login for Business → Settings → Valid OAuth Redirect URIs**; Threads: use case settings;
   Pinterest: app redirect URIs). Google and LinkedIn keep their `http://localhost` URIs, which they accept.
8. Check it: open `https://studio.hrpai.in/api/v1/health` in a browser. It shows `"status":"ok"`. Opening
   `https://studio.hrpai.in/` shows "Not found", which is expected.

The tunnel runs only while the app runs. Scheduled posts publish from this laptop, so keep the app running at
posting times as before.


<!-- guide:inbox -->
## Inbox: reading and answering comments

The Inbox collects comments, replies and mentions every 10 minutes. It needs a few extra permissions; after
adding them, **reconnect** the account on Social accounts so the new permission is granted to the token.

| Platform | Add this permission | Where |
|---|---|---|
| Facebook Page | `pages_manage_engagement` (reply and like) | Meta app → Use cases → **Manage everything on your Page** → Customize → Add |
| Instagram | `instagram_manage_comments` (read, reply, @mentions) | Meta app → Use cases → **Manage messaging and content on Instagram** → Customize → Add |
| Threads | `threads_read_replies`, `threads_manage_replies` | Meta app → Use cases → **Access the Threads API** → Customize → Add |
| YouTube | `https://www.googleapis.com/auth/youtube.force-ssl` | Google Cloud → Google Auth Platform → **Data access** → Add or remove scopes |
| Mastodon | `read:notifications`, `write:statuses` (and `write:favourites` to like) | Your server → Preferences → Development → your app; then add the account again with the new token |
| Bluesky | nothing extra | The app password already allows it |

Then: Facebook and Instagram → press **Connect Facebook and Instagram** again (tick Instagram); Threads → **Connect
Threads**; Google → **Connect** Google. Until you do, the Inbox shows "needs permission …" on that account and
everything else keeps working.

LinkedIn and Pinterest don't give apps like this one access to comments, so the Inbox lists your recent posts there
with an **Open** link instead.

Mentions monitoring (Mentions tab → Listening settings) uses your web search providers (Integrations → Search),
Bluesky search, Mastodon hashtag timelines and, optionally, YouTube search (100 quota units per keyword) and Instagram
hashtag search (only when Meta has granted your app **Instagram Public Content Access**; otherwise it's skipped).
