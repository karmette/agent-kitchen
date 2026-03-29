package main

import (
	"fmt"
	"strconv"
	"strings"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

var (
	inputContainer = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#7D56F4")).
			Padding(1, 3)

	labelStyle = lg.NewStyle().
			Foreground(lg.Color("#7D56F4")).
			Bold(true)

	sublabelStyle = lg.NewStyle().
			Foreground(lg.Color("#555555"))

	hintStyle = lg.NewStyle().
			Foreground(lg.Color("#3993ED")).
			Italic(true)

	instructionStyle = lg.NewStyle().
				Foreground(lg.Color("#444444"))

	rubricHeaderStyle = lg.NewStyle().
				Foreground(lg.Color("#3993ED")).
				Bold(true)

	rubricDimStyle = lg.NewStyle().
			Foreground(lg.Color("#555555"))

	stepNumStyle = lg.NewStyle().
			Foreground(lg.Color("#7D56F4")).
			Bold(true)

	stepTextStyle = lg.NewStyle().
			Foreground(lg.Color("#888888"))

	stepActiveStyle = lg.NewStyle().
			Foreground(lg.Color("#CCCCCC"))
)

const logo = `    ___                    __
   /   | ____ ____  ____  / /_
  / /| |/ __ ` + "`" + `/ _ \/ __ \/ __/
 / ___ / /_/ /  __/ / / / /_
/_/  |_\__, /\___/_/ /_/\__/
      /____/
    __ __ _  __       __
   / //_/(_)/ /______/ /_  ___  ____
  / ,<  / // __/ ___/ __ \/ _ \/ __ \
 / /| |/ // /_/ /__/ / / /  __/ / / /
/_/ |_/_/ \__/\___/_/ /_/\___/_/ /_/`

// ── Rubric criterion ────────────────────────────────────────────────────────

type criterion struct {
	nameTi   textinput.Model
	weightTi textinput.Model
	descTi   textinput.Model
}

func newCriterion() criterion {
	n := textinput.New()
	n.Placeholder = "e.g. rapport"
	n.SetWidth(16)

	w := textinput.New()
	w.Placeholder = "30"
	w.SetWidth(4)

	d := textinput.New()
	d.Placeholder = "0 = hostile, 1 = wants to work together"
	d.SetWidth(40)

	return criterion{nameTi: n, weightTi: w, descTi: d}
}

// ── Input model ─────────────────────────────────────────────────────────────

type InputModel struct {
	textInput    textinput.Model
	iterationsTi textinput.Model
	cellsTi      textinput.Model
	done         bool
	zone         int

	rubricCustom bool
	criteria     []criterion
	rubricRow    int
	rubricCol    int
}

func NewInputModel(iterations, cells int) InputModel {
	ti := textinput.New()
	ti.Placeholder = "e.g. best negotiator, best teacher, best salesperson"
	ti.Focus()
	ti.SetWidth(55)

	iterTi := textinput.New()
	iterTi.Placeholder = "5"
	iterTi.SetWidth(8)

	cellsTi := textinput.New()
	cellsTi.Placeholder = "3"
	cellsTi.SetWidth(8)

	return InputModel{
		textInput:    ti,
		iterationsTi: iterTi,
		cellsTi:      cellsTi,
		zone:         0,
		criteria:     []criterion{newCriterion(), newCriterion(), newCriterion()},
	}
}

func (m *InputModel) SetWidth(w int) {
	width := 55
	if w < 65 {
		width = w - 10
	}
	m.textInput.SetWidth(width)
	descW := width - 24
	if descW < 10 {
		descW = 10
	}
	for i := range m.criteria {
		m.criteria[i].descTi.SetWidth(descW)
	}
}

func (m InputModel) Init() tea.Cmd {
	return textinput.Blink
}

func (m *InputModel) addRowZone() int { return 2 + len(m.criteria) }
func (m *InputModel) gensZone() int {
	if m.rubricCustom {
		return m.addRowZone() + 1
	}
	return 2
}
func (m *InputModel) cellsZone() int { return m.gensZone() + 1 }
func (m *InputModel) maxZone() int   { return m.cellsZone() }
func (m *InputModel) inRubric() bool {
	return m.rubricCustom && m.zone >= 2 && m.zone < m.addRowZone()
}
func (m *InputModel) onAddRow() bool {
	return m.rubricCustom && m.zone == m.addRowZone()
}

// ── Update ──────────────────────────────────────────────────────────────────

func (m InputModel) Update(msg tea.Msg) (InputModel, tea.Cmd) {
	var cmd tea.Cmd
	handled := false

	switch msg := msg.(type) {
	case tea.KeyPressMsg:
		key := msg.String()

		switch key {
		case "ctrl+c", "esc":
			return m, tea.Quit

		case "enter":
			if m.zone == 1 {
				handled = true
			} else if m.onAddRow() {
				m.criteria = append(m.criteria, newCriterion())
				m.rubricRow = len(m.criteria) - 1
				m.rubricCol = 0
				m.zone = 2 + m.rubricRow
				m.syncFocus()
				handled = true
			} else if m.textInput.Value() == "" {
				// Can't start without a goal
				handled = true
			} else {
				m.done = true
				return m, nil
			}

		case "tab", "down":
			m.moveDown()
			handled = true

		case "shift+tab", "up":
			m.moveUp()
			handled = true

		case "left":
			if m.zone == 1 {
				m.rubricCustom = false
				handled = true
			} else if m.inRubric() && m.rubricCol > 0 {
				m.rubricCol--
				m.syncFocus()
				handled = true
			}

		case "right":
			if m.zone == 1 {
				m.rubricCustom = true
				handled = true
			} else if m.inRubric() && m.rubricCol < 2 {
				m.rubricCol++
				m.syncFocus()
				handled = true
			}

		case "ctrl+d":
			if m.rubricCustom && m.inRubric() && len(m.criteria) > 1 {
				m.criteria = append(m.criteria[:m.rubricRow], m.criteria[m.rubricRow+1:]...)
				if m.rubricRow >= len(m.criteria) {
					m.rubricRow = len(m.criteria) - 1
				}
				m.zone = 2 + m.rubricRow
				m.syncFocus()
				handled = true
			}
		}
	}

	if !handled {
		switch {
		case m.zone == 0:
			m.textInput, cmd = m.textInput.Update(msg)
		case m.inRubric():
			r := m.rubricRow
			if r < len(m.criteria) {
				switch m.rubricCol {
				case 0:
					m.criteria[r].nameTi, cmd = m.criteria[r].nameTi.Update(msg)
				case 1:
					m.criteria[r].weightTi, cmd = m.criteria[r].weightTi.Update(msg)
				case 2:
					m.criteria[r].descTi, cmd = m.criteria[r].descTi.Update(msg)
				}
			}
		case m.zone == m.gensZone():
			m.iterationsTi, cmd = m.iterationsTi.Update(msg)
		case m.zone == m.cellsZone():
			m.cellsTi, cmd = m.cellsTi.Update(msg)
		}
	}

	return m, cmd
}

func (m *InputModel) moveDown() {
	if m.inRubric() {
		m.rubricRow++
		if m.rubricRow >= len(m.criteria) {
			m.rubricRow = len(m.criteria) - 1
			m.zone = m.addRowZone()
		} else {
			m.zone = 2 + m.rubricRow
		}
	} else if m.zone < m.maxZone() {
		m.zone++
		if m.inRubric() {
			m.rubricRow = 0
			m.rubricCol = 0
		}
	}
	m.syncFocus()
}

func (m *InputModel) moveUp() {
	if m.onAddRow() {
		m.rubricRow = len(m.criteria) - 1
		m.zone = 2 + m.rubricRow
	} else if m.inRubric() {
		m.rubricRow--
		if m.rubricRow < 0 {
			m.rubricRow = 0
			m.zone = 1
		} else {
			m.zone = 2 + m.rubricRow
		}
	} else if m.zone > 0 {
		m.zone--
		if m.onAddRow() {
			m.rubricRow = len(m.criteria) - 1
			m.zone = 2 + m.rubricRow
		} else if m.inRubric() {
			m.rubricRow = len(m.criteria) - 1
			m.zone = 2 + m.rubricRow
		}
	}
	m.syncFocus()
}

func (m *InputModel) syncFocus() {
	m.blurAll()
	switch {
	case m.zone == 0:
		m.textInput.Focus()
	case m.inRubric():
		r := m.rubricRow
		if r < len(m.criteria) {
			switch m.rubricCol {
			case 0:
				m.criteria[r].nameTi.Focus()
			case 1:
				m.criteria[r].weightTi.Focus()
			case 2:
				m.criteria[r].descTi.Focus()
			}
		}
	case m.zone == m.gensZone():
		m.iterationsTi.Focus()
	case m.zone == m.cellsZone():
		m.cellsTi.Focus()
	}
}

func (m *InputModel) blurAll() {
	m.textInput.Blur()
	m.iterationsTi.Blur()
	m.cellsTi.Blur()
	for i := range m.criteria {
		m.criteria[i].nameTi.Blur()
		m.criteria[i].weightTi.Blur()
		m.criteria[i].descTi.Blur()
	}
}

// ── View ────────────────────────────────────────────────────────────────────

func (m InputModel) View() string {
	logoRendered := lg.NewStyle().
		Foreground(lg.Color("#7D56F4")).
		Bold(true).
		Render(logo)

	// Pipeline visualization — this IS the product explanation
	pipeline := lg.JoinVertical(lg.Left,
		"",
		hintStyle.Render("evolve AI agents through"),
		hintStyle.Render("simulated natural selection"),
		"",
		stepNumStyle.Render("1")+stepTextStyle.Render("  describe the agent you want"),
		stepNumStyle.Render("2")+stepTextStyle.Render("  we generate diverse test scenarios"),
		stepNumStyle.Render("3")+stepTextStyle.Render("  a population competes in simulations"),
		stepNumStyle.Render("4")+stepTextStyle.Render("  an LLM judge scores each agent"),
		stepNumStyle.Render("5")+stepTextStyle.Render("  top agents survive, mutate, breed"),
		stepNumStyle.Render("6")+stepTextStyle.Render("  repeat — the best agent emerges"),
	)

	header := lg.JoinHorizontal(lg.Top, logoRendered, "   ", pipeline)

	// Toggle
	var toggleLine string
	if m.zone == 1 {
		if m.rubricCustom {
			toggleLine = sublabelStyle.Render("auto  ") + labelStyle.Render("[ custom ]")
		} else {
			toggleLine = labelStyle.Render("[ auto ]") + sublabelStyle.Render("  custom")
		}
	} else {
		if m.rubricCustom {
			toggleLine = sublabelStyle.Render("auto  ") + sublabelStyle.Render("[ custom ]")
		} else {
			toggleLine = sublabelStyle.Render("[ auto ]") + sublabelStyle.Render("  custom")
		}
	}

	parts := []string{
		header,
		"",
		labelStyle.Render("  Goal"),
		m.textInput.View(),
		"",
		"  " + labelStyle.Render("Rubric") + "  " + toggleLine,
	}

	if m.rubricCustom {
		parts = append(parts, m.viewRubricForm())
	} else {
		parts = append(parts,
			sublabelStyle.Render("  an LLM generates scoring criteria from your goal"))
	}

	// Settings — compact inline layout
	gensLabel := sublabelStyle
	cellsLabel := sublabelStyle
	if m.zone == m.gensZone() {
		gensLabel = labelStyle
	}
	if m.zone == m.cellsZone() {
		cellsLabel = labelStyle
	}

	gensBlock := lg.JoinVertical(lg.Left,
		gensLabel.Render("Generations"),
		m.iterationsTi.View(),
		sublabelStyle.Render("rounds of evolution"),
	)

	cellsBlock := lg.JoinVertical(lg.Left,
		cellsLabel.Render("Scenarios"),
		m.cellsTi.View(),
		sublabelStyle.Render("situations to test in"),
	)

	settings := lg.JoinHorizontal(lg.Top, "  ", gensBlock, "        ", cellsBlock)

	parts = append(parts,
		"",
		settings,
		"",
		instructionStyle.Render("  enter to evolve · ↑↓ navigate · esc quit"),
	)

	s := lg.JoinVertical(lg.Left, parts...)
	return inputContainer.Render(s)
}

func (m InputModel) viewRubricForm() string {
	var rows []string

	rows = append(rows, sublabelStyle.Render("  agents ranked by weighted score across all scenarios"))
	rows = append(rows, "")

	header := fmt.Sprintf("  %-17s %-5s %s",
		rubricHeaderStyle.Render("Name"),
		rubricHeaderStyle.Render("Wt%"),
		rubricHeaderStyle.Render("Description (what does 0 vs 1 look like?)"))
	rows = append(rows, header)

	totalWeight := 0
	for i, c := range m.criteria {
		isFocused := m.inRubric() && m.rubricRow == i

		prefix := "  "
		if isFocused && len(m.criteria) > 1 {
			prefix = lg.NewStyle().Foreground(lg.Color("#FF5555")).Render("⊖ ")
		}

		row := fmt.Sprintf("%s%s %s %s",
			prefix, c.nameTi.View(), c.weightTi.View(), c.descTi.View())
		rows = append(rows, row)

		w, _ := strconv.Atoi(c.weightTi.Value())
		totalWeight += w
	}

	addStyle := rubricDimStyle
	if m.onAddRow() {
		addStyle = labelStyle
	}

	totalColor := rubricDimStyle
	totalLabel := fmt.Sprintf("total: %d%%", totalWeight)
	if totalWeight == 100 {
		totalColor = lg.NewStyle().Foreground(lg.Color("#81F88F"))
		totalLabel = fmt.Sprintf("total: %d%% ✓", totalWeight)
	} else if totalWeight > 0 && totalWeight != 100 {
		totalColor = lg.NewStyle().Foreground(lg.Color("#FFB347"))
	}

	addAndTotal := addStyle.Render("  + add criterion") +
		strings.Repeat(" ", 30) + totalColor.Render(totalLabel)
	rows = append(rows, addAndTotal)

	return strings.Join(rows, "\n")
}

// ── Getters ─────────────────────────────────────────────────────────────────

func (m InputModel) GetRubric() string {
	if !m.rubricCustom {
		return ""
	}

	var parts []string
	for _, c := range m.criteria {
		name := c.nameTi.Value()
		weight := c.weightTi.Value()
		desc := c.descTi.Value()
		if name == "" {
			continue
		}
		line := name
		if weight != "" {
			line += fmt.Sprintf(" (weight: %s%%)", weight)
		}
		if desc != "" {
			line += ": " + desc
		}
		parts = append(parts, line)
	}

	if len(parts) == 0 {
		return ""
	}
	return "Score the agent on these criteria (each 0.0 to 1.0):\n" +
		strings.Join(parts, "\n")
}

func (m InputModel) GetIterations() int {
	val, err := strconv.Atoi(m.iterationsTi.Value())
	if err != nil || val < 1 {
		return 5
	}
	return val
}

func (m InputModel) GetCells() int {
	val, err := strconv.Atoi(m.cellsTi.Value())
	if err != nil || val < 1 {
		return 3
	}
	return val
}
