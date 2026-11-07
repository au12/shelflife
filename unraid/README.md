# Unraid Community Apps files

Everything Community Apps needs to list Shelflife, laid out like Unraid's
[starter repository](https://github.com/unraid/unraid-community-apps-starter):

| File | What it is |
| --- | --- |
| `ca_profile.xml` | Repository profile shown in Community Apps (required, non-empty `<Profile>`) |
| `templates/shelflife.xml` | The Docker template users install from |
| `icon.svg` | Repository icon referenced by `ca_profile.xml` |
| `LICENSE` | MIT, an OSI-approved license |

## Installing without Community Apps

In the Unraid Docker tab choose **Add Container** and paste this into **Template**:

```
https://raw.githubusercontent.com/au12/shelflife/main/unraid/templates/shelflife.xml
```

## Submitting to Community Apps

Community Apps reads `ca_profile.xml` and `LICENSE` from the **root** of the repository you submit. This folder is that root, so copy its contents into the root of the repository you submit (for example a dedicated `au12/unraid-templates` repo that can hold templates for other apps too). Keep each template's `<TemplateURL>` pointing at the raw URL of that exact file in whichever repository it ends up in.

Before submitting:

1. The GitHub repository you submit must be public, and the `ghcr.io/au12/shelflife` package must be public so Unraid can pull it without signing in.
2. Go to https://ca.unraid.net/submit/new, run **Validate** and **Scan**, and fix anything they flag.
3. Optionally open a support thread on the Unraid forums and add it as `<Support>` in the template and `<Forum>` in `ca_profile.xml`. Until then, support points at GitHub issues.
4. Submit for review.

Re-run Validate and Scan after any meaningful change to the XML.

Reference: [Community Apps submission help](https://ca.unraid.net/submit/help).
