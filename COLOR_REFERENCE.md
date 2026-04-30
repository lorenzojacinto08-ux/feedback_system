# Color Reference

## Primary Gradient Blue
- **Start:** `#4A90E2` (Medium Blue)
- **End:** `#1e40af` (Dark Blue)
- **Usage:** Header background, login page, message header, buttons
- **Gradient:** `linear-gradient(135deg, #4A90E2 0%, #1e40af 100%)`

## Accent Blue Gradient (Message Bubbles)
- **Start:** `#4A90E2` (Medium Blue)
- **End:** `#357ABD` (Blue)
- **Usage:** Client message bubbles, avatar gradients
- **Gradient:** `linear-gradient(135deg, #4A90E2 0%, #357ABD 100%)`

## Yellow Accent (CTA Buttons)
- **Primary:** `#facc15` (Yellow-400)
- **Secondary:** `#eab308` (Yellow-500)
- **Dark:** `#ca8a04` (Yellow-600)
- **Gradient:** `linear-gradient(135deg, #facc15, #eab308)`
- **Usage:** Primary action buttons, highlights

## Neutral Colors
- **White:** `#FFFFFF`, `#fff`
- **Gray-50:** `#f8f9fa`, `#f8fafc`
- **Gray-100:** `#e9ecef`, `#f1f5f9`
- **Gray-200:** `#dee2e6`, `#e2e8f0`
- **Gray-300:** `#ced4da`
- **Gray-400:** `#adb5bd`, `#94a3b8`
- **Gray-500:** `#6c757d`, `#64748b`
- **Gray-600:** `#495057`, `#475569`
- **Gray-700:** `#343a40`, `#334155`
- **Gray-800:** `#212529`, `#1e293b`
- **Gray-900:** `#0f172a`

## Status Colors
- **Success:** `#22c55e`, `#16a34a`, `#047857`
- **Danger:** `#dc2626`, `#ef4444`
- **Warning:** `#f97316`, `#f59e0b`
- **Info:** `#3b82f6`, `#0d6efd`

## CSS Variables (layout.html)
```css
--accent-50 through --accent-500: #4A90E2
--accent-600 through --accent-900: #357ABD
--header-color: #4A90E2
--yellow-400: #facc15
--yellow-500: #EAB308
--yellow-600: #ca8a04
```

## Page-Specific Colors

### Header (header.html)
- Background: `linear-gradient(135deg, #4A90E2 0%, #1e40af 100%)`

### Login Page (auth/login.html)
- Background: `linear-gradient(135deg, #4A90E2 0%, #1e40af 100%)`
- Header: `linear-gradient(135deg, #4A90E2 0%, #1e40af 100%)`

### Client Support (client/support.html)
- Message Header: `linear-gradient(135deg, #4A90E2 0%, #1e40af 100%)`
- Client Message Bubble: `linear-gradient(135deg, #4A90E2 0%, #357ABD 100%)`

### Change Password (account/change_password.html)
- Hero: `linear-gradient(135deg, #1f2937 0%, #4A90E2 100%)`
- Submit Button: `linear-gradient(135deg, #facc15, #eab308)`

### Admin Messages (admin/messages.html)
- Active Conversation: `rgba(13,110,253,.08)` with `#0d6efd` border
- Send Button: `#0d6efd`

### Survey Error (survey_error.html)
- Background: `linear-gradient(135deg, #667eea 0%, #764ba2 100%)`
- Icon: `#4A90E2`
