package main

import (
	"time"

	lg "charm.land/lipgloss/v2"
)

var (
	modelStyle = lg.NewStyle().
			Foreground(lg.Color("#CCCCCC"))

	adversaryStyle = lg.NewStyle().
			Foreground(lg.Color("#CCCC00"))
)

type conversation struct {
	ch   chan string
	done chan struct{}
}

func newConversation() conversation {
	return conversation{
		ch:   make(chan string),
		done: make(chan struct{}),
	}
}

func (c *conversation) start() {
	stop := time.NewTimer(time.Minute)
	ticker := time.NewTicker(200 * time.Millisecond)

	state := true

outer:
	for {
		select {
		case <-stop.C:
			break outer

		case <-ticker.C:
			if state {
				// c.ch <- modelStyle.Render("Model: haii\n")
				c.ch <- "Model: haii\n"
			} else {
				c.ch <- "Adversary: grr\n"
			}
			state = !state
		}
	}

	close(c.done)
}
