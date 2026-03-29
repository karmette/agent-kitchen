package main

import (
	"fmt"
	"math"
	"os"

	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

type sessionState int

const (
	inputView sessionState = iota
	resultView
)

type RootModel struct {
	state  sessionState
	input  InputModel
	result *ResultModel
	prompt string
	width  int
	height int
}

func (m RootModel) Init() tea.Cmd {
	return m.input.Init()
}

func (m RootModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.input.SetWidth(m.width)
	}

	switch m.state {
	case inputView:
		m.input, cmd = m.input.Update(msg)

		if m.input.done {
			m.prompt = m.input.textInput.Value()
			cells := m.input.GetCells()
			iterations := m.input.GetIterations()
			m.state = resultView
			cols := int(math.Ceil(math.Sqrt(float64(cells))))
			rows := int(math.Ceil(float64(cells) / float64(cols)))
			if cols < 1 {
				cols = 1
			}
			if rows < 1 {
				rows = 1
			}
			m.result = NewResultModel(rows, cols, iterations)
			m.result.width = m.width
			m.result.height = m.height
		}

	case resultView:
		cmd = m.result.Update(msg)
		if cmd == nil && m.result != nil {
			cmd = m.result.Init()
		}
	}

	return m, cmd
}

func (m RootModel) View() tea.View {
	var subView string

	switch m.state {
	case inputView:
		subView = m.input.View()
	case resultView:
		subView = m.result.View()
	}

	style := lg.NewStyle().
		Width(m.width).
		Height(m.height).
		Align(lg.Center, lg.Center)

	return tea.NewView(style.Render(subView))
}

func main() {
	p := tea.NewProgram(RootModel{
		state:  inputView,
		input:  NewInputModel(10, 25),
		result: &ResultModel{},
	})

	if _, err := p.Run(); err != nil {
		fmt.Printf("Error: %v", err)
		os.Exit(1)
	}
}
