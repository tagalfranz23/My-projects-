# Use Case Diagram Implementation Matrix

The diagram is implemented with the existing security precedence: Barangay Staff may review, validate, update, and endorse operational records, but only Administrators may perform final approval/rejection, manage roles/settings, send broad notifications, and generate system reports.

| Use case | Resident | Barangay Staff | Administrator | Implementation |
|---|---:|---:|---:|---|
| Sign Up / Register | Yes | No administrative signup | Creates legitimate accounts | `/register`, `/users` |
| Log In / Log Out | Yes | Yes | Yes | `/login`, CSRF-protected `/logout` |
| Manage own profile/password | Yes | Yes | Yes | `/profile` |
| Apply for Permit | Own | No | No | `/permits` |
| Review Permit Applications | View own status | Review/endorse | Full review | `/permits`, centralized transitions |
| Approve / Reject Permits | No | **No** | Yes | Admin-only transitions |
| Request Event Approval | Own | No | No | `/events` |
| Review Event Requests | View own status | Review/endorse | Full review | `/events`, centralized transitions |
| Approve / Reject Events | No | **No** | Yes | Admin-only transitions |
| File Blotter Complaint | Own | Staff may encode intake | Administrator may encode/manage | `/blotters` |
| Manage / Update Blotter | View own | Intake/review/investigation | Full/final decisions | `/blotters/<id>` |
| View Status | Own transactions | Operational module lists | System-wide module lists | `/status`, module lists |
| Receive Notifications | Own | Own/operational | Own/system | `/notifications` |
| Schedule Appointment | Own related records | Operational records | All authorized records | `/schedules` |
| Manage Appointments | No final override | Confirm/reschedule/complete | Full including cancellation | schedule transition matrix |
| Send Notifications | Automatic transaction triggers | Authorized workflow triggers | Direct and workflow notifications | `/notifications/send` |
| Manage Users | No | Read-only list | Full creation/status | `/users` |
| Manage Roles/Permissions | No | No | Assign defined roles | `/users/<id>/role`, centralized RBAC |
| Manage Announcements | View | View | Create/delete | `/announcements` |
| System Settings | No | No | Yes | `/configuration` |
| Generate Reports | No | No | Yes | `/reports/export/<format>` |
| Activity Logs | No | No | Yes | `/activity-logs` |

All protected use cases are checked server-side. Navigation visibility is not treated as authorization, and residents retain ownership isolation for permits, events, blotters, schedules, and notifications.
