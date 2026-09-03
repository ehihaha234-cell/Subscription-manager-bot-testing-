"""Interactive single-message Help Center for clone-bot sellers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


HELP_PAGES = {
    "getting_started": """🚀 Getting Started

What does this guide do?

This guide helps you prepare the Clone Bot for selling subscriptions and delivering private access automatically.

━━━━━━━━━━━━━━
How to set it up

1. Admin Panel → Manage Plans → Add Plan
Create at least one plan with name, price and duration.

2. Admin Panel → Channels / Groups
Connect every private channel or group included in your plans.

3. Admin Panel → Payment Settings
Enable an automatic gateway or configure Manual Payment with UPI/QR.

4. Admin Panel → Bot Settings → Welcome Message
Add the welcome text, media and buttons users will see.

5. Open the Clone Bot from another Telegram account and test purchase, approval and invite-link delivery.

━━━━━━━━━━━━━━
Important Notes

• Add the Clone Bot as administrator in connected chats.
• Give it invite-link and member-management permissions.
• Keep at least one plan and one payment method enabled.
• Test all buttons before sharing the bot publicly.""",

    "plans_payments": """💳 Plans & Payments

What does this feature do?

Plans define what users can buy, how much they pay, how long access lasts and which chats they can join. Payment Settings control how those payments are collected.

━━━━━━━━━━━━━━
Manage Plans

Admin Panel → Manage Plans

• Add Plan: create a new subscription plan.
• Edit Plan: change name, price, duration or included chats.
• Enable / Disable: show or hide a plan from users.
• Delete Plan: permanently remove an unused plan.

Create a plan, enter the requested details, assign connected channels/groups and save it.

━━━━━━━━━━━━━━
Payment Settings

Admin Panel → Payment Settings

• Automatic Payment Gateway: add supported gateway credentials.
• Manual Payment: enable UPI screenshot payments.
• UPI ID / Name / QR: add the payment details shown to users.
• Pending Payments: approve or reject submitted proofs.
• Payment History: review processed transactions.

━━━━━━━━━━━━━━
Important Notes

• A plan must be enabled before users can buy it.
• Assign at least one connected destination where required.
• Never share gateway secret keys outside the secure setup screen.
• Test one real or supported test payment before launch.""",

    "channels": """🔗 Channels / Groups

What does this feature do?

It connects private Telegram channels and groups to the Clone Bot so valid subscribers can receive private invite links and expired users can be managed.

━━━━━━━━━━━━━━
How to connect

1. Open the required channel or group.
2. Add the Clone Bot as administrator.
3. Enable invite-link and member-management permissions.
4. Send /connectgroup inside that chat.
5. Return to Admin Panel → Channels / Groups.
6. Verify the chat name and assign it to the required plans.

━━━━━━━━━━━━━━
Available controls

Admin Panel → Channels / Groups

• View connected chats.
• Remove a connected chat.
• Assign chats to plans.
• Resend Invite Links to Active Subscribers.

━━━━━━━━━━━━━━
Important Notes

• Use /connectgroup inside the exact chat you want to connect.
• The bot cannot create links without Invite Users permission.
• The bot cannot remove expired users without member-management permission.
• Removing a chat does not automatically repair existing plan assignments.""",

    "bot_settings": """⚙️ Bot Settings

What does this section do?

Bot Settings controls the Clone Bot name, user-facing Welcome Message, support contact, currency, timezone, reminder timing and referral settings.

━━━━━━━━━━━━━━
🤖 Bot Name

Admin Panel → Bot Settings → Bot Name

Change the display name used by the Clone Bot in supported messages and pages. Enter the new name and save it.

━━━━━━━━━━━━━━
💬 Welcome Message

Admin Panel → Bot Settings → Welcome Message

Edit the welcome text shown to users. From the same editor, you can add or remove media, add URL buttons, add Feature Buttons, rename buttons, arrange the button layout and preview the final welcome.

• Text: write the main welcome content.
• Media: add supported photo/video media.
• URL Buttons: add a button name and complete URL.
• Feature Buttons: add Plans, Buy, Renew, Profile, Referral, Support or Home.
• Preview: check the final message before saving or publishing.

━━━━━━━━━━━━━━
📞 Support Username

Admin Panel → Bot Settings → Support Username

Set the Telegram username users should contact for help. Enter a valid username, normally in @username format, and save it.

━━━━━━━━━━━━━━
💵 Currency

Admin Panel → Bot Settings → Currency

Choose the currency shown in plan prices, payment messages and supported payment pages. Select the required currency and confirm it.

Important: Changing the currency label does not automatically convert existing plan prices. Review plan prices after changing it.

━━━━━━━━━━━━━━
🕒 Timezone

Admin Panel → Bot Settings → Timezone

Select the timezone used for subscription expiry dates, scheduled actions, payment dates and other displayed times. Choose your region and save it.

Important: Confirm the timezone before scheduling broadcasts or checking expiry times.

━━━━━━━━━━━━━━
🔔 Reminder Days

Admin Panel → Bot Settings → Reminder Days

Set how many days before subscription expiry the user should receive an expiry reminder. Enter the required number of days and save it.

Example: Setting 3 sends the reminder approximately 3 days before expiry when the reminder system runs.

━━━━━━━━━━━━━━
🎁 Referral Reward Days

Admin Panel → Bot Settings → Referral Reward Days

Set how many subscription days a user receives as a referral reward after completing the configured referral requirement. Enter the reward duration and save it.

Important: This setting controls reward duration; referral eligibility and counting rules are managed through Referral Unlock.

━━━━━━━━━━━━━━
🔓 Referral Unlock

Admin Panel → Bot Settings → Referral Unlock

Configure the referral-based access system. From this section you can enable or disable referral unlock, set the required referral count, select when referrals are counted, choose the reward duration/destination and manage eligible channel or group invite links where available.

Test the referral flow using separate accounts before enabling it for all users.

━━━━━━━━━━━━━━
📜 Terms & Policy

Admin Panel → Terms & Policy

Review or update the Terms, Privacy, Refund and Support information shown for the Clone Bot.

━━━━━━━━━━━━━━
Important Notes

• Always preview the Welcome Message after changing text, media or buttons.
• Feature Buttons require their related feature to be configured.
• Use a valid Telegram username and complete URLs.
• Check plan prices after changing currency.
• Check scheduled times and expiry dates after changing timezone.
• Branding is controlled by the platform owner and is not a Seller Bot Settings option.""",

    "broadcast": """📢 Broadcast

What does this feature do?

Broadcast sends one message to all or selected Clone Bot users. It supports text, supported media, URL buttons and feature buttons.

━━━━━━━━━━━━━━
📤 Send Broadcast

Admin Panel → Broadcast

1. Select the target audience.
2. Send or create the broadcast content.
3. Add URL or Feature Buttons if required.
4. Preview the final message.
5. Confirm Send Broadcast.

The sending job runs in the background so other bot buttons can remain responsive.

━━━━━━━━━━━━━━
🗓 Scheduled Broadcast

Admin Panel → Scheduled

Create the message, choose the requested date/time and confirm. Use the schedule list to review or cancel pending items.

━━━━━━━━━━━━━━
🔁 Retry Failed

Admin Panel → Retry Failed

Choose a previous failed broadcast. Only failed recipients are retried; successful recipients should not receive the same retry again.

━━━━━━━━━━━━━━
Feature Button Back Navigation

When users open a feature from a broadcast and press Back, the original broadcast message should be restored instead of the normal Welcome Message.

━━━━━━━━━━━━━━
Important Notes

• Blocked/deleted users may fail permanently.
• Telegram limits can slow large broadcasts.
• Keep the hosting service online for scheduled delivery.
• Review the final Total, Sent and Failed report.""",

    "live_support": """💬 Live Support

What does this section do?

Live Support lets Clone Bot users contact the seller/support team. User messages are delivered to a connected topic-enabled support group, and staff replies return to the user.

━━━━━━━━━━━━━━
💬 Support Setup

Admin Panel → Live Support

1. Turn Support ON.
2. Create a Telegram group and enable Topics.
3. Add the Clone Bot as administrator.
4. Send /connectsupport inside that group.
5. Return to Live Support and verify the connected group.

Each user should keep one support topic, with later messages going to the same topic.

━━━━━━━━━━━━━━
🤖 Auto Reply

Admin Panel → Live Support → Auto Reply

Add a trigger keyword, then configure text, media, URL buttons or Feature Buttons. Save and enable it. The response should be sent only when the configured keyword matches.

━━━━━━━━━━━━━━
📝 Reply Template

Admin Panel → Live Support → Reply Template

Create a shortcut/keyword and its saved response. A support admin uses that shortcut inside the user’s support topic to send the configured text/media/buttons.

When a user opens a feature from a template and presses Back, the original template message should return.

━━━━━━━━━━━━━━
🧹 Template Auto Remove

Open the Template Auto Remove setting inside Live Support and choose whether the staff shortcut message should be removed after sending the template.

━━━━━━━━━━━━━━
⚙️ Settings / Connected Group

Use Live Support settings to enable/disable support, review the connected group and manage available topic/template behavior.

━━━━━━━━━━━━━━
📊 Statistics

Open Live Support Statistics to review recorded support activity where available.

━━━━━━━━━━━━━━
Important Notes

• Topics must be enabled before /connectsupport.
• Give the bot the required group permissions.
• Business Automation chats are not forwarded into Live Support.""",

    "business_automation": """💼 Business Automation

What does this section do?

Business Automation connects supported Telegram accounts and automates private-chat welcome messages, auto replies and reply templates separately from Clone Bot Live Support.

━━━━━━━━━━━━━━
📱 Connect Telegram Account

Admin Panel → Business Automation → Connect Telegram Account

Follow the connection flow, provide the requested Telegram API/account verification details and wait for Connected status. Multiple accounts may be supported by the current plan/configuration.

━━━━━━━━━━━━━━
🔌 Connected Accounts

Admin Panel → Business Automation → Connected Accounts

View account status and disconnect an account when required. Reconnect if its session becomes invalid.

━━━━━━━━━━━━━━
📝 Welcome Message

Admin Panel → Business Automation → Welcome Message

Configure text, supported media, URL buttons and Feature Buttons, then enable the welcome. It is sent according to the Welcome Once and other settings.


━━━━━━━━━━━━━━
🤖 Auto Reply

Admin Panel → Business Automation → Auto Reply

Add a keyword and response content. It may contain text, media, URL buttons and Feature Buttons. Enable it after saving. It should reply only when the configured keyword matches.

━━━━━━━━━━━━━━
📝 Reply Template

Admin Panel → Business Automation → Reply Template

Create a shortcut/keyword used by the connected-account owner to send a saved response. Feature Button → Back should restore the original template.

━━━━━━━━━━━━━━
⚙️ Settings

Admin Panel → Business Automation → Settings

Available settings may include Welcome Once, Ignore Own Messages, anti-loop protection, flood protection, working hours and reply delay. Configure only the options needed for your workflow.

━━━━━━━━━━━━━━
📊 Statistics

Admin Panel → Business Automation → Statistics

Review stored user/conversation and automation activity where available.

━━━━━━━━━━━━━━
Important Notes

• Keep account sessions secure.
• Test keyword matching before enabling widely.
• Working hours, delay or flood protection can prevent an expected reply.
• This module works separately from Live Support.""",

    "users_staff": """👥 Users & Staff

👥 User Management

What does this feature do?

It lets authorized admins search Clone Bot users and manage their account/access records.

Admin Panel → User Management

• Search by numeric Telegram ID or username.
• View subscription details.
• Give, extend, suspend or remove access where available.
• Ban or unban users.
• Resend private invite links.
• Review relevant payment/subscription records.

━━━━━━━━━━━━━━
👮 Staff Management

What does this feature do?

It allows the seller to grant selected Admin Panel access to trusted team members.

Admin Panel → Staff Management

1. Add Staff.
2. Enter/select the Telegram user.
3. Choose the available role or permissions.
4. Save.
5. Remove access when no longer required.

━━━━━━━━━━━━━━
Important Notes

• Only the seller can manage staff.
• Give only the permissions required.
• Manual subscription changes can immediately affect connected-chat access.
• Confirm the correct Telegram account before banning or removing access.""",

    "content_protection": """🔒 Content Protection

What does this feature do?

Content Protection sends supported new Clone Bot messages/media with Telegram protection enabled, reducing normal forwarding or saving options where Telegram supports it.

━━━━━━━━━━━━━━
How to configure

Admin Panel → Content Protection

Use the available ON/OFF control and save the required protection setting.

Protection may apply to newly sent supported content such as:
• Welcome content
• Broadcasts and scheduled broadcasts
• Live Support replies
• Payment/subscription messages

━━━━━━━━━━━━━━
Important Notes

• It applies only to newly sent supported messages.
• It does not modify messages already sent.
• Content posted directly in connected channels/groups is not automatically changed.
• Telegram protection reduces normal sharing options but cannot guarantee that content will never be copied by other methods.""",

    "deleting_messages": """🗑 Deleting Messages

What does this section do?

It automatically removes selected commands, links, media and Telegram service messages from connected groups.

━━━━━━━━━━━━━━
How to open

Admin Panel → Deleting Messages

Enable the main system, then turn on only the filters you need.

━━━━━━━━━━━━━━
⌨️ Delete Commands

Configure deletion of selected admin/user commands sent in the group.

🔗 Delete Links

Enable all-link deletion, selected platform links or Custom Domains. Add domains exactly as requested by the editor.

📦 Delete Forwarded / Media

Available filters can include forwarded messages, photos, videos, GIFs, documents/files, audio, voice, stickers and video notes.

⚙️ Delete Service Messages

Available filters can include join/leave notices, pinned-message notices, topic messages, boosts and other supported Telegram service events.

🛡 Safety Controls

Use Ignore Admins and Ignore Seller/Owner so trusted management messages are not removed where supported.

📊 Statistics

Open Statistics to review recorded deletion activity.

♻️ Reset

Use Reset only when you want to clear the configured deletion rules/settings.

━━━━━━━━━━━━━━
Important Notes

• The Clone Bot needs Delete Messages permission.
• Test rules in a separate group first.
• Enabling broad filters may remove legitimate user content.
• Custom domains and platform filters should not be enabled together unless intended.""",

    "subscription_guard": """🛡 Subscription Guard

What does this feature do?

Subscription Guard checks joins and known members against Clone Bot subscription records, then removes users who do not have valid access according to the enabled rules.

━━━━━━━━━━━━━━
How to configure

Admin Panel → Subscription Guard

🟢 Enable / Disable
Turn real-time protection on or off.

🔄 Force Sync
Check users already known to the Clone Bot database against current subscription status.

📋 Logs
Review recorded guard actions and reasons.

📊 Statistics
View totals for checks, removals or related actions where recorded.

🔔 Notifications
Enable or disable seller notifications for guard actions where available.

♻️ Reset
Clear guard logs/statistics/settings only when you understand the displayed confirmation.

━━━━━━━━━━━━━━
Users may be removed for

• Expired or inactive subscription
• Banned status
• Unauthorized join
• Invalid/used invite-link conditions supported by the current configuration

━━━━━━━━━━━━━━
Important Notes

• The bot needs member-management permissions.
• Owners/admins/whitelisted users may be skipped.
• Force Sync is limited to users known in the bot database; Telegram does not always provide a complete member list to bots.""",

    "coupons_referral": """🎟 Coupons & Referrals

🎟 Coupons

What does this feature do?

Coupons give eligible users a configured discount on selected subscription purchases.

Admin Panel → Coupons

1. Create Coupon.
2. Set the coupon code.
3. Choose discount type/value.
4. Set usage limit and expiry where available.
5. Select applicable plans.
6. Enable and save.

A coupon works only while all configured conditions are valid.

━━━━━━━━━━━━━━
🤝 Seller Referral

Admin Panel → Seller Referral

Use this section for seller/platform referral controls available to the Clone Bot seller.

━━━━━━━━━━━━━━
🎁 User Referral

The user Referral feature lets users share a personal referral link and receive configured rewards.

Configure the available referral controls for:
• Enable / Disable
• Required referral count
• Count mode, such as new start or completed purchase
• Reward duration
• Reward destination channels/groups
• Per-destination invite-link enable/disable

━━━━━━━━━━━━━━
Important Notes

• Test coupon calculations before public use.
• Referral rewards should count only valid unique users.
• Purchase-based referral rewards require a successful approved payment.""",

    "statistics": """📊 Statistics

What does this feature do?

Statistics shows recorded Clone Bot activity so the seller can review users, subscriptions, payments, revenue and feature usage.

━━━━━━━━━━━━━━
How to open

Admin Panel → Statistics

Available totals may include:
• Total bot users
• Active and expired subscribers
• Today and total revenue
• Payment records
• Plan activity
• Broadcast delivery results
• Live Support activity
• Business Automation activity
• Subscription Guard actions

━━━━━━━━━━━━━━
Important Notes

• Statistics depend on data successfully recorded in the database.
• Manual or external actions not recorded by the bot may not appear.
• Revenue totals depend on approved/successful payment records and the configured currency.""",

    "commands": """📋 Commands

/start
Seller/admin: opens the Admin Panel according to access.
Normal user: opens the Clone Bot Welcome Menu.

/admin
Opens the Clone Bot Admin Panel for an authorized seller/staff member.

/help
Opens this Help Center.

/connectgroup
Run inside a channel/group after adding the Clone Bot as administrator. Connects that chat for subscription access.

/connectsupport
Run inside the topic-enabled Live Support group after adding the Clone Bot as administrator.

/version
Shows the currently deployed runtime version.

━━━━━━━━━━━━━━
Important Notes

• Connection commands must be sent inside the target group/channel.
• Commands do not bypass seller/staff permission checks.
• Internal development callbacks are intentionally not listed.""",

    "troubleshooting": """❓ Troubleshooting

Button does not respond
• Wait briefly and tap once.
• Send /start or /admin.
• Confirm the latest ZIP is deployed.
• Check Render/hosting logs for the exact error.

Channel/group will not connect
• Add the Clone Bot as administrator.
• Enable invite-link/member-management permissions.
• Run /connectgroup inside the correct chat.

Invite link is not delivered
• Verify the user has an active subscription.
• Verify the plan includes the destination.
• Confirm the bot can create invite links.

Broadcast fails or is slow
• Large broadcasts may take time due to Telegram limits.
• Blocked/deleted users fail.
• Use Retry Failed after completion.
• Check that the background task started in logs.

Live Support does not work
• Enable Topics in the support group.
• Recheck admin permissions.
• Run /connectsupport again.

Business Automation does not reply
• Verify the Telegram account is Connected.
• Enable the required Welcome/Auto Reply feature.
• Recheck keyword, working hours, delay and flood protection.

Payment problem
• Verify enabled payment method and credentials.
• Check Pending Payments and hosting logs.

━━━━━━━━━━━━━━
When reporting an error, include the screenshot and the exact log lines shown at the same time.""",
}


HELP_LABELS = {
    "getting_started": "🚀 Getting Started",
    "plans_payments": "💳 Plans & Payments",
    "channels": "🔗 Channels / Groups",
    "bot_settings": "⚙️ Bot Settings",
    "broadcast": "📢 Broadcast",
    "live_support": "💬 Live Support",
    "business_automation": "💼 Business Automation",
    "users_staff": "👥 Users & Staff",
    "content_protection": "🔒 Content Protection",
    "deleting_messages": "🗑 Deleting Messages",
    "subscription_guard": "🛡 Subscription Guard",
    "coupons_referral": "🎟 Coupons & Referral",
    "statistics": "📊 Statistics",
    "commands": "📋 Commands",
    "troubleshooting": "❓ Troubleshooting",
}


def help_home_text() -> str:
    return (
        "🆘 Help & Commands\n\n"
        "Welcome to the Clone Bot Help Center.\n\n"
        "Select a section to learn what each feature does, where to find it, "
        "how to configure it and which important points to check.\n\n"
        "All Help Center buttons edit this same message; no extra help message is created."
    )


def help_home_keyboard() -> InlineKeyboardMarkup:
    order = list(HELP_LABELS.items())
    rows = []
    for index in range(0, len(order), 2):
        row = [InlineKeyboardButton(order[index][1], callback_data=f"a_help_{order[index][0]}")]
        if index + 1 < len(order):
            row.append(InlineKeyboardButton(order[index + 1][1], callback_data=f"a_help_{order[index + 1][0]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="a_home")])
    return InlineKeyboardMarkup(rows)


def help_page_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Help Center", callback_data="a_help")],
        [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="a_home")],
    ])
