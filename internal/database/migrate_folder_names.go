package database

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"gorm.io/gorm"
	"quill/internal/models"
	"quill/pkg/logger"
	"quill/pkg/slug"
)

var shortIDSuffix = regexp.MustCompile(`-[0-9a-f]{8}$`)
var legacyContactSeparator = regexp.MustCompile(`--[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]+$`)

// MigrateHumanReadableFolderNames migrates only legacy folders owned by database
// records. Never enumerate the archive or open metadata files during startup:
// opening an iCloud/File Provider directory can block indefinitely, even when
// stat succeeds. Unregistered folders belong to the user and are left alone.
func MigrateHumanReadableFolderNames(db *gorm.DB) error {
	var vaults []models.Vault
	if err := db.Find(&vaults).Error; err != nil {
		return err
	}
	for _, vault := range vaults {
		if strings.TrimSpace(vault.Path) == "" {
			continue
		}
		var jobs []models.TranscriptionJob
		if err := db.Select("id", "title", "audio_path", "artifact_dir", "transcript_json_path", "transcript_markdown_path").Where("vault_id = ?", vault.ID).Find(&jobs).Error; err != nil {
			return err
		}
		bundles := map[string]string{}
		for _, job := range jobs {
			paths := []string{job.AudioPath}
			for _, p := range []*string{job.ArtifactDir, job.TranscriptJSONPath, job.TranscriptMarkdownPath} {
				if p != nil {
					paths = append(paths, *p)
				}
			}
			for _, p := range paths {
				name := ownedFolder(vault.Path, "Transcripts", p)
				if !shortIDSuffix.MatchString(name) {
					continue
				}
				title := humanizeSlug(shortIDSuffix.ReplaceAllString(name, ""))
				if job.Title != nil && strings.TrimSpace(*job.Title) != "" {
					title = strings.TrimSpace(*job.Title)
				}
				bundles[name] = title
			}
		}
		names := make([]string, 0, len(bundles))
		for name := range bundles {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			oldRel := filepath.Join("Transcripts", name)
			newRel, err := renameOwnedFolder(vault.Path, oldRel, bundles[name])
			if err != nil {
				logger.Warn("migrate-folder-names: bundle deferred", "error", err)
				continue
			}
			if newRel == "" {
				continue
			}
			err = db.Transaction(func(tx *gorm.DB) error {
				for _, job := range jobs {
					updates := map[string]interface{}{}
					addMigratedPath(updates, "audio_path", job.AudioPath, vault.Path, oldRel, newRel)
					for col, p := range map[string]*string{"artifact_dir": job.ArtifactDir, "transcript_json_path": job.TranscriptJSONPath, "transcript_markdown_path": job.TranscriptMarkdownPath} {
						if p != nil {
							addMigratedPath(updates, col, *p, vault.Path, oldRel, newRel)
						}
					}
					if len(updates) > 0 {
						if err := tx.Model(&models.TranscriptionJob{}).Where("id = ? AND vault_id = ?", job.ID, vault.ID).Updates(updates).Error; err != nil {
							return err
						}
					}
				}
				return nil
			})
			if err != nil {
				return rollbackFolderRename(vault.Path, oldRel, newRel, err)
			}
		}
		var contacts []models.Contact
		if err := db.Select("id", "name", "note_path", "voice_snippet_path", "signature_embedding_path").Where("vault_id = ?", vault.ID).Find(&contacts).Error; err != nil {
			return err
		}
		for _, contact := range contacts {
			name := ownedFolder(vault.Path, filepath.Join("Contacts", "People"), contact.NotePath)
			if !legacyContactSeparator.MatchString(name) {
				continue
			}
			oldRel := filepath.Join("Contacts", "People", name)
			title := contact.Name
			if strings.TrimSpace(title) == "" {
				title = humanizeSlug(legacyContactSeparator.ReplaceAllString(name, ""))
			}
			newRel, err := renameOwnedFolder(vault.Path, oldRel, title)
			if err != nil {
				logger.Warn("migrate-folder-names: contact deferred", "error", err)
				continue
			}
			if newRel == "" {
				continue
			}
			updates := map[string]interface{}{}
			addMigratedPath(updates, "note_path", contact.NotePath, vault.Path, oldRel, newRel)
			for col, p := range map[string]*string{"voice_snippet_path": contact.VoiceSnippetPath, "signature_embedding_path": contact.SignatureEmbeddingPath} {
				if p != nil {
					addMigratedPath(updates, col, *p, vault.Path, oldRel, newRel)
				}
			}
			if err := db.Model(&models.Contact{}).Where("id = ? AND vault_id = ?", contact.ID, vault.ID).Updates(updates).Error; err != nil {
				return rollbackFolderRename(vault.Path, oldRel, newRel, err)
			}
		}
	}
	return nil
}

// Restore the original folder if its database path update cannot commit.
func rollbackFolderRename(vault, oldRel, newRel string, cause error) error {
	if err := os.Rename(filepath.Join(vault, newRel), filepath.Join(vault, oldRel)); err != nil {
		return fmt.Errorf("folder path update failed: %v; restoring original folder also failed: %w", cause, err)
	}
	return cause
}

// ownedFolder accepts only a direct child of the expected vault subtree.
func ownedFolder(vault, subtree, path string) string {
	if strings.TrimSpace(path) == "" {
		return ""
	}
	if filepath.IsAbs(path) {
		var err error
		path, err = filepath.Rel(vault, path)
		if err != nil {
			return ""
		}
	}
	relative, err := filepath.Rel(subtree, filepath.Clean(path))
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return ""
	}
	return strings.Split(relative, string(filepath.Separator))[0]
}

func renameOwnedFolder(vault, oldRel, title string) (string, error) {
	source := filepath.Join(vault, oldRel)
	info, err := os.Lstat(source)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", nil
	}
	base := slug.SafeFilename(title, "Transcript")
	parent := filepath.Dir(source)
	// Probe candidate names, not the parent directory. os.ReadDir is the operation
	// that hung the packaged app before its HTTP server could start.
	for suffix := 1; suffix < 10000; suffix++ {
		name := base
		if suffix > 1 {
			name = fmt.Sprintf("%s %d", base, suffix)
		}
		target := filepath.Join(parent, name)
		if target == source {
			return "", nil
		}
		if _, err = os.Lstat(target); err == nil {
			continue
		} else if !os.IsNotExist(err) {
			return "", err
		}
		if err = os.Rename(source, target); err != nil {
			return "", err
		}
		return filepath.Join(filepath.Dir(oldRel), name), nil
	}
	return "", fmt.Errorf("no available migration target")
}

func addMigratedPath(updates map[string]interface{}, column, path, vault, oldRel, newRel string) {
	oldPrefix, newPrefix := oldRel, newRel
	if filepath.IsAbs(path) {
		oldPrefix = filepath.Join(vault, oldRel)
		newPrefix = filepath.Join(vault, newRel)
	}
	clean := filepath.Clean(path)
	if clean == oldPrefix || strings.HasPrefix(clean, oldPrefix+string(filepath.Separator)) {
		updates[column] = newPrefix + strings.TrimPrefix(clean, oldPrefix)
	}
}

// humanizeSlug converts "talk-with-ian" back to "Talk with Ian".
func humanizeSlug(s string) string {
	words := strings.Split(s, "-")
	for i, w := range words {
		if w == "" {
			continue
		}
		words[i] = strings.ToUpper(w[:1]) + w[1:]
	}
	result := strings.Join(words, " ")
	return strings.TrimSpace(result)
}

// extractFrontmatterValue extracts a value from YAML frontmatter.
func extractFrontmatterValue(content, key string) string {
	lines := strings.Split(content, "\n")
	if len(lines) < 3 || strings.TrimSpace(lines[0]) != "---" {
		return ""
	}
	prefix := key + ":"
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			break
		}
		trimmed := strings.TrimSpace(lines[i])
		if strings.HasPrefix(trimmed, prefix) {
			val := strings.TrimSpace(strings.TrimPrefix(trimmed, prefix))
			// Remove surrounding quotes if present.
			val = strings.Trim(val, `"'`)
			return val
		}
	}
	return ""
}
