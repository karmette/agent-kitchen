package main

import (
	"strconv"

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

	instructionStyle = lg.NewStyle().
				Foreground(lg.Color("#444444"))
)

const logo = `
   ▄▀█ █▀▀ █▀▀ █▄░█ ▀█▀
   █▀█ █▄█ ██▄ █░▀█ ░█░

   █▄▀ █ ▀█▀ █▀▀ █░█ █▀▀ █▄░█
   █░█ █ ░█░ █▄▄ █▀█ ██▄ █░▀█`

type InputModel struct {
	textInput    textinput.Model
	rubricTi     textinput.Model
	iterationsTi textinput.Model
	cellsTi      textinput.Model
	done         bool
	focusedField int
}

func NewInputModel(iterations, cells int) InputModel {
	ti := textinput.New()
	ti.Placeholder = "e.g. best negotiator, best teacher, best salesperson"
	ti.Focus()
	ti.SetWidth(50)

	rubricTi := textinput.New()
	rubricTi.Placeholder = "e.g. empathy, closes deals, creative solutions"
	rubricTi.SetWidth(50)

	iterTi := textinput.New()
	iterTi.Placeholder = "5"
	iterTi.SetWidth(50)

	cellsTi := textinput.New()
	cellsTi.Placeholder = "3"
	cellsTi.SetWidth(50)

	return InputModel{
		textInput:    ti,
		rubricTi:     rubricTi,
		iterationsTi: iterTi,
		cellsTi:      cellsTi,
		done:         false,
		focusedField: 0,
	}
}

func (m *InputModel) SetWidth(w int) {
	width := 50
	if w < 60 {
		width = w - 10
	}
	m.textInput.SetWidth(width)
	m.rubricTi.SetWidth(width)
	m.iterationsTi.SetWidth(width)
	m.cellsTi.SetWidth(width)
}

func (m InputModel) Init() tea.Cmd {
	return textinput.Blink
}

func (m InputModel) Update(msg tea.Msg) (InputModel, tea.Cmd) {
	var cmd tea.Cmd
	handled := false

	switch msg := msg.(type) {
	case tea.KeyPressMsg:
		switch msg.String() {
		case "ctrl+c", "esc":
			return m, tea.Quit
		case "enter":
			m.done = true
			return m, nil
		case "up", "shift+tab":
			if m.focusedField > 0 {
				m.focusedField--
				m.updateFocus()
			}
			handled = true
		case "down", "tab":
			if m.focusedField < 3 {
				m.focusedField++
				m.updateFocus()
			}
			handled = true
		}
	}

	if !handled {
		switch m.focusedField {
		case 0:
			m.textInput, cmd = m.textInput.Update(msg)
		case 1:
			m.rubricTi, cmd = m.rubricTi.Update(msg)
		case 2:
			m.iterationsTi, cmd = m.iterationsTi.Update(msg)
		case 3:
			m.cellsTi, cmd = m.cellsTi.Update(msg)
		}
	}
	return m, cmd
}

func (m *InputModel) updateFocus() {
	fields := []*textinput.Model{&m.textInput, &m.rubricTi, &m.iterationsTi, &m.cellsTi}
	for i, f := range fields {
		if i == m.focusedField {
			f.Focus()
		} else {
			f.Blur()
		}
	}
}

func (m InputModel) View() string {
	logoRendered := lg.NewStyle().
		Foreground(lg.Color("#7D56F4")).
		Bold(true).
		Render(logo)

	tagline := lg.NewStyle().
		Foreground(lg.Color("#3993ED")).
		Italic(true).
		Render("   replace prompt engineering with evolution")

	s := lg.JoinVertical(lg.Left,
		logoRendered,
		"",
		tagline,
		"",
		"",
		labelStyle.Render("  What agent do you want to evolve?"),
		m.textInput.View(),
		"",
		sublabelStyle.Render("  Optimize for (leave blank for auto)"),
		m.rubricTi.View(),
		"",
		sublabelStyle.Render("  Generations"),
		m.iterationsTi.View(),
		"",
		sublabelStyle.Render("  Scenarios (cells)"),
		m.cellsTi.View(),
		"",
		instructionStyle.Render("  enter to evolve • ↑↓ navigate • esc quit"),
	)
	return inputContainer.Render(s)
}

func (m InputModel) GetRubric() string {
	return m.rubricTi.Value()
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
